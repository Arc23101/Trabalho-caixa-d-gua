import queue 
import threading 
import paho.mqtt.client as mqtt 
import time

BROKER  = "broker.mqttdashboard.com" 
PREFIXO = "iot/turmaManha/grupoAntonyMalu/" 

eventos = queue.Queue()     
teclado = queue.Queue()     
estado  = {"nivel": None, "bomba": None, "esp": None} 
confirmacao_bomba = threading.Event() 
enchendo = False 
 
def on_connect(client, userdata, flags, reason_code, properties): 
    if reason_code.is_failure: 
        print("Falha ao conectar:", reason_code) 
        return 
    print("Conectado ao broker MQTT com sucesso!") 
    client.subscribe(PREFIXO + "#", qos=1) 
 
def on_message(client, userdata, msg): 
    topico = msg.topic[len(PREFIXO):] 
    texto = msg.payload.decode(errors="replace") 
    
    if topico == "bomba/estado" and texto == "LIGADA": 
        confirmacao_bomba.set() 
        
    eventos.put((topico, texto))         
 
def ler_teclado(): 
    while True: 
        teclado.put(input().strip())     
 
def pode_ligar(): 
    return (estado["esp"] == "online" and estado["nivel"] == "BAIXO" 
            and estado["bomba"] == "DESLIGADA") 
 
def mostrar_menu(): 
    if pode_ligar(): 
        print("\n*** Nível BAIXO detectado! ***")
        print("Digite [1] para LIGAR a bomba ou [2] para aguardar.") 
 
def ligar_bomba(): 
    global enchendo 
    confirmacao_bomba.clear() 
    cliente.publish(PREFIXO + "bomba/comando", "LIGAR", qos=1, retain=False) 
    print("Comando enviado. Aguardando confirmação da ESP...") 
    
    if confirmacao_bomba.wait(timeout=5): 
        enchendo = True 
        print(">>> Bomba LIGADA (confirmado pela ESP)") 
    else: 
        print("!!! A ESP não confirmou em 5 s. Verifique a conexão.") 
 
def tratar_evento(topico, texto): 
    global enchendo 
    if topico == "caixa/nivel": 
        estado["nivel"] = texto 
        print(f"[{time.strftime('%H:%M:%S')}] Nível: {texto}") 
        if texto == "ALTO" and enchendo: 
            enchendo = False 
            print("Caixa CHEIA. Bomba desligada pela ESP. Voltando ao modo de supervisão.") 
    elif topico == "bomba/estado": 
        estado["bomba"] = texto 
        print(f"[{time.strftime('%H:%M:%S')}] Bomba: {texto}") 
    elif topico == "esp/status": 
        estado["esp"] = texto 
        print(f"[{time.strftime('%H:%M:%S')}] Status da ESP: {texto}") 
    elif topico == "caixa/alerta": 
        print(f"[{time.strftime('%H:%M:%S')}] ALERTA: {texto}") 
        
    if topico in ("caixa/nivel", "bomba/estado", "esp/status"): 
        mostrar_menu() 
 
cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pc-supervisor-AntonyMalu") 
cliente.on_connect = on_connect 
cliente.on_message = on_message 
cliente.connect(BROKER, 1883, keepalive=60) 
cliente.loop_start()                                        # thread de rede 
 
threading.Thread(target=ler_teclado, daemon=True).start()   # thread do teclado 

print("Iniciando estação de supervisão...")
print(f"Conectando ao broker {BROKER}...")
 
try: 
    while True: 
        try: 
            topico, texto = eventos.get(timeout=0.1) 
            tratar_evento(topico, texto) 
        except queue.Empty: 
            pass 
 
        try: 
            opcao = teclado.get_nowait() 
        except queue.Empty: 
            continue 
            
        if opcao == "1": 
            if pode_ligar(): 
                ligar_bomba() 
            else: 
                print("Opção indisponível: exige nível BAIXO, bomba desligada e ESP online.") 
        elif opcao == "2": 
            print("Aguardando. A opção segue disponível enquanto o nível estiver BAIXO.") 
            
except KeyboardInterrupt: 
    print("\nEncerrando...") 
finally: 
    cliente.loop_stop() 
    cliente.disconnect()