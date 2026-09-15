from machine import Pin, time_pulse_us 
import time 

  

trig = Pin(21, Pin.OUT, value=0) 

echo = Pin(19, Pin.IN) 

  

TIMEOUT_US = 25000   # limita o bloqueio (~4 m de alcance, sobra para a caixa) 

  

def ler_distancia_cm(): 

    trig.value(0) 

    time.sleep_us(2) 

    trig.value(1) 

    time.sleep_us(10)          # pulso de gatilho de 10 us 

    trig.value(0) 

    t = time_pulse_us(echo, 1, TIMEOUT_US) 

    if t < 0:                  # -2: eco não começou | -1: eco não terminou 

        return None            # leitura inválida, mas o programa segue 

    d = t / 58.3               # ida e volta, velocidade do som ~343 m/s 

    if d < 2 or d > 400:       # fora da faixa física do sensor 

        return None 

    return d 

leituras = [] 

  

def filtrar(d): 

    leituras.append(d) 

    if len(leituras) > 5: 

        leituras.pop(0) 

    ordenadas = sorted(leituras) 

    return ordenadas[len(ordenadas) // 2]


led_verde    = Pin(25, Pin.OUT, value=0) 

led_vermelho = Pin(26, Pin.OUT, value=0) 

led_bomba    = Pin(27, Pin.OUT, value=0) 

# Adicione a boia no pino 33 (com pull-up, igual à emergência)
chave_boia   = Pin(33, Pin.IN, Pin.PULL_UP)

falhas_consecutivas = 0

distancia_atual = 0.0

t_pisca = 0

pisca_vermelho = False
  

PREFIXO    = b"iot/turmaManha/grupoAntonyMalu/" 

T_NIVEL    = PREFIXO + b"caixa/nivel" 

T_ALERTA   = PREFIXO + b"caixa/alerta" 

T_BOMBA    = PREFIXO + b"bomba/estado" 

T_STATUS   = PREFIXO + b"esp/status"

T_DISTANCIA   = PREFIXO + b"caixa/distancia" 

TOPICO_CMD = PREFIXO + b"bomba/comando" 

  

estado     = "MONITORANDO"   # estado atual da máquina de estados 

nivel      = None            # "ALTO", "MEDIO", "BAIXO" ou "ERRO" 

bomba      = False           # RF18: a ESP sempre inicia com a bomba desligada 

emergencia = False 

cmd_ligar  = False           # flag marcada pelo callback do MQTT 

conectado  = False           # indica se o MQTT está conectado 



def processar_leitura(distancias):
    global falhas_consecutivas, distancia_atual, estado
    
    if distancias is None:
        falhas_consecutivas += 1
        if falhas_consecutivas >= 15 and estado not in ["FALHA_SENSOR", "EMERGENCIA"]:
            estado = "FALHA_SENSOR"
            if bomba:
                desligar_bomba(b"FALHA DO SENSOR: bomba desligada")
            else:
                publicar(T_ALERTA, b"FALHA DO SENSOR: leituras invalidas")
        return 
    
    falhas_consecutivas = 0
    if estado == "FALHA_SENSOR":
        estado = "MONITORANDO"
        publicar(T_ALERTA, b"Sensor voltou a funcionar. Monitorando")
    
    distancia_atual = filtrar(distancias)
    

def publicar(topico, msg, retain=False): 

    # Publicar NUNCA pode derrubar a lógica local (RF13) 

    global conectado 

    if not conectado: 

        return                       # sem rede: a lógica local segue normalmente 

    try: 

        cliente.publish(topico, msg, retain=retain) 

    except OSError: 

        conectado = False            # o laço principal tentará reconectar 

  

def ligar_bomba(): 

    global bomba, estado 

    bomba = True 

    led_bomba.value(1) 

    estado = "ENCHENDO" 

    publicar(T_BOMBA, b"LIGADA", retain=True) 

    publicar(T_ALERTA, b"Bomba LIGADA por comando do supervisor") 

  

def desligar_bomba(motivo): 

    global bomba 

    bomba = False 

    led_bomba.value(0) 

    publicar(T_BOMBA, b"DESLIGADA", retain=True) 

    publicar(T_ALERTA, motivo) 

  

def ao_receber(topico, msg): 

    # Callback do MQTT: só marca a flag, não toma decisão 

    global cmd_ligar 

    if topico == TOPICO_CMD and msg == b"LIGAR": 

        cmd_ligar = True 


def atualizar_estado():
    global nivel, t_pisca, pisca_vermelho
    
    if estado == "FALHA_SENSOR":
        agora = time.ticks_ms()
        if time.ticks_diff(agora, t_pisca) >= 500:  
            t_pisca = agora
            pisca_vermelho = not pisca_vermelho
            led_vermelho.value(1 if pisca_vermelho else 0)
        return 

    novo_nivel = nivel
    
    if distancia_atual > 0:
        if nivel == "ALTO" or nivel is None:
            if distancia_atual > 9.5 and distancia_atual < 19.0:
                novo_nivel = "MEDIO"
            elif distancia_atual >= 19.0:
                novo_nivel = "BAIXO"
                
        elif nivel == "MEDIO":
            if distancia_atual <= 8.0:
                novo_nivel = "ALTO"
            elif distancia_atual >= 19.0:
                novo_nivel = "BAIXO"
                
        elif nivel == "BAIXO":
            if distancia_atual < 17.5 and distancia_atual > 8.0:
                novo_nivel = "MEDIO"
            elif distancia_atual <= 8.0:
                novo_nivel = "ALTO"
                
    if novo_nivel != nivel:
        ao_mudar_nivel(novo_nivel)
        

def processar_comando(): 

    # Chamada a cada volta do laço principal 

    global cmd_ligar 

    if not cmd_ligar: 

        return 

    cmd_ligar = False                # consome a flag 

    if emergencia: 

        publicar(T_ALERTA, b"Comando rejeitado: emergencia ativa") 

    elif estado != "AGUARDANDO_DECISAO": 

        publicar(T_ALERTA, b"Comando rejeitado: nivel nao esta BAIXO") 

    else: 

        ligar_bomba()
def atualizar_leds(): 

    led_verde.value(1 if nivel == "MEDIO" else 0) 

    led_vermelho.value(1 if nivel == "BAIXO" else 0) 

    led_bomba.value(1 if bomba else 0) 

  

def ao_mudar_nivel(novo): 

    global nivel, estado 

    nivel = novo 

    publicar(T_NIVEL, novo.encode(), retain=True) 

    atualizar_leds() 

  

    if estado == "ENCHENDO" and novo == "ALTO": 

        # Decisão LOCAL: não depende da rede nem do PC 

        desligar_bomba(b"Caixa CHEIA: bomba desligada. Supervisor volta a monitorar") 

        estado = "MONITORANDO" 

    elif estado == "MONITORANDO" and novo == "BAIXO": 

        estado = "AGUARDANDO_DECISAO" 

        publicar(T_ALERTA, b"Nivel BAIXO: aguardando decisao do supervisor") 

    elif estado == "AGUARDANDO_DECISAO" and novo != "BAIXO": 

        # O nível subiu sem a bomba (por exemplo, a caixa foi enchida manualmente) 

        estado = "MONITORANDO" 

        publicar(T_ALERTA, b"Nivel " + novo.encode()) 

    else: 

        publicar(T_ALERTA, b"Nivel " + novo.encode())

import time, micropython 

micropython.alloc_emergency_exception_buf(100) 

  

chave_emerg   = Pin(32, Pin.IN, Pin.PULL_UP) 

flag_emerg    = False 

t_emerg       = None 

emerg_estavel = chave_emerg.value() 

def publicar_distancia():
    if estado != "FALHA_SENSOR" and distancia_atual > 0:
        msg = "{:.1f}".format(distancia_atual)
        publicar(T_DISTANCIA, msg.encode(), retain=False)

def isr_emerg(pin): 

    global flag_emerg 

    flag_emerg = True                # SÓ marca! Nada de print ou publish aqui 

chave_emerg.irq(trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING, handler=isr_emerg) 

flag_boia    = False
t_boia       = None
boia_estavel = chave_boia.value()

def isr_boia(pin):
    global flag_boia
    flag_boia = True 

chave_boia.irq(trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING, handler=isr_boia)


def tratar_emergencia(ativa): 

    global emergencia, estado 

    emergencia = ativa 

    if ativa: 

        if bomba: 

            desligar_bomba(b"PARADA DE EMERGENCIA: bomba desligada") 

        else: 

            publicar(T_ALERTA, b"PARADA DE EMERGENCIA acionada") 

        estado = "EMERGENCIA" 

    else: 

        publicar(T_ALERTA, b"Emergencia liberada") 

        if nivel == "BAIXO": 

            estado = "AGUARDANDO_DECISAO" 

            publicar(T_ALERTA, b"Nivel BAIXO: aguardando nova decisao do supervisor") 

        else: 

            estado = "MONITORANDO" 

  

def tratar_chaves():
    global flag_emerg, t_emerg, emerg_estavel
    global flag_boia, t_boia, boia_estavel
    
    agora = time.ticks_ms()
    
    if flag_emerg:
        flag_emerg = False
        t_emerg = agora
    if t_emerg is not None and time.ticks_diff(agora, t_emerg) >= 50:
        t_emerg = None
        v_emerg = chave_emerg.value()
        if v_emerg != emerg_estavel:
            emerg_estavel = v_emerg
            tratar_emergencia(v_emerg == 0)
            
    if flag_boia:
        flag_boia = False
        t_boia = agora
    if t_boia is not None and time.ticks_diff(agora, t_boia) >= 50:
        t_boia = None
        v_boia = chave_boia.value()
        if v_boia != boia_estavel:
            boia_estavel = v_boia
            if v_boia == 0:
                if bomba:
                    desligar_bomba(b"transbordamento iminente: possivel falha")
                else:
                    publicar(T_ALERTA, b"Boia mecanica acionada")
                    
                    
import network, time, ubinascii, machine 

from umqtt.simple import MQTTClient 

  

SSID      = "Wokwi-GUEST" 

SENHA     = "" 

BROKER    = "broker.mqttdashboard.com"

CLIENT_ID = b"esp-" + ubinascii.hexlify(machine.unique_id()) 

  

wlan = network.WLAN(network.STA_IF) 

t_tentativa = 0 

  

def conectar_mqtt(): 

    global cliente, conectado 

    cliente = MQTTClient(CLIENT_ID, BROKER, keepalive=60) 

    cliente.set_last_will(T_STATUS, b"offline", retain=True) 

    cliente.set_callback(ao_receber)     # sempre ANTES do subscribe 

    cliente.connect() 

    cliente.subscribe(TOPICO_CMD, qos=1) # assinar de novo a cada conexão 

    cliente.publish(T_STATUS, b"online", retain=True) 

    # Republica o estado atual: mensagens podem ter se perdido com a rede fora 

    cliente.publish(T_BOMBA, b"LIGADA" if bomba else b"DESLIGADA", retain=True) 

    if nivel: 

        cliente.publish(T_NIVEL, nivel.encode(), retain=True) 

    conectado = True 

  

def tentar_reconectar(): 

    # Chamada no laço principal quando conectado == False 

    global conectado, t_tentativa 

    agora = time.ticks_ms() 

    if time.ticks_diff(agora, t_tentativa) < 5000: 

        return                           # ainda não é hora de tentar de novo 

    t_tentativa = agora 

    try: 

        if not wlan.isconnected(): 

            wlan.connect(SSID, SENHA)    # não espera aqui; confere na próxima tentativa 

            return 

        conectar_mqtt() 

        print("MQTT reconectado") 

    except OSError as e: 

        conectado = False 

        print("Falha ao reconectar:", e) 

  

# ---- Inicialização (só aqui é aceitável esperar alguns segundos) ---- 

wlan.active(True) 

wlan.connect(SSID, SENHA) 

for _ in range(20):                      # espera até ~10 s pelo Wi-Fi 

    if wlan.isconnected(): 

        break 

    time.sleep_ms(500) 

try: 

    conectar_mqtt() 

except OSError: 

    conectado = False                    # segue sem rede; o laço tenta de novo


t_leitura = t_telemetria = time.ticks_ms() 

  

while True: 

    agora = time.ticks_ms() 

  

    # 1. Rede: verifica mensagens sem bloquear 

    try: 

        cliente.check_msg() 

    except OSError: 

        tentar_reconectar()              # tenta reconectar sem travar a lógica local 

  

    # 2. Chaves: trata as flags das interrupções (com debounce) 

    tratar_chaves() 

  

    # 3. Sensor: uma leitura a cada 200 ms 

    if time.ticks_diff(agora, t_leitura) >= 200: 

        t_leitura = agora 

        processar_leitura(ler_distancia_cm()) 

  

    # 4. Máquina de estados: decide, atualiza LEDs, publica mudanças 

    atualizar_estado() 

  

    # 5. Telemetria periódica (também mantém o keepalive) 

    if time.ticks_diff(agora, t_telemetria) >= 5000: 

        t_telemetria = agora 

        publicar_distancia() 

  

    time.sleep_ms(20)             # pausa curta, apenas para não ocupar 100% da CPU 
    
