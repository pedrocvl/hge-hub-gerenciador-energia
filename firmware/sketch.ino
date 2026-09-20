#include <WiFi.h>
#include <PubSubClient.h>
const char *TOPICO_BASE = "hge/estacio-rp";
String topicoTelemetria=String(TOPICO_BASE)+"/telemetria";
String topicoComando=String(TOPICO_BASE)+"/comando/+";
const int PINOS_SENSORES[3]={34,35,32}, PINOS_RELES[3]={25,26,27};
bool ligados[3]={true,true,true};
float correntes[3]={}, potencias[3]={};
double energias[3]={};
WiFiClient rede;
PubSubClient mqtt(rede);
unsigned long leitura=0, envio=0, tentativa=0;
String entrada;
void medir(){
 unsigned long agora=millis();
 float horas=(agora-leitura)/3600000.0;
 leitura=agora;
 for(int i=0;i<3;i++){
  energias[i]+=potencias[i]*horas;
  correntes[i]=ligados[i]?analogRead(PINOS_SENSORES[i])*20.0/4095.0:0;
  potencias[i]=127.0*correntes[i];
 }
}
void publicar(){
 medir();
 String json="{\"tensao\":127.0,\"circuitos\":[";
 for(int i=0;i<3;i++){
  if(i) json+=',';
  json+="{\"id\":"+String(i+1);
  json+=",\"ligado\":"+String(ligados[i]?"true":"false");
  json+=",\"corrente\":"+String(correntes[i],2);
  json+=",\"potencia\":"+String(potencias[i],2);
  json+=",\"energia_wh\":"+String(energias[i],3)+'}';
 }
 json+="]}";
 Serial.println(json);
 if(mqtt.connected()) mqtt.publish(topicoTelemetria.c_str(),json.c_str());
 envio=millis();
}
void acionar(int i,bool estado){
 medir(); ligados[i]=estado;
 digitalWrite(PINOS_RELES[i],estado?HIGH:LOW);
 publicar();
}
void receber(char* topico,byte* dados,unsigned int tamanho){
 String comando;
 for(unsigned int j=0;j<tamanho;j++) comando+=(char)dados[j];
 for(int i=0;i<3;i++){
  if(String(topico)!=String(TOPICO_BASE)+"/comando/"+String(i+1)) continue;
  if(comando=="ON") acionar(i,true);
  else if(comando=="OFF") acionar(i,false);
 }
}
void setup(){
 Serial.begin(115200); analogReadResolution(12);
 for(int i=0;i<3;i++){
  pinMode(PINOS_SENSORES[i],INPUT); pinMode(PINOS_RELES[i],OUTPUT);
  digitalWrite(PINOS_RELES[i],HIGH);
 }
 mqtt.setServer("broker.hivemq.com",1883);
 mqtt.setCallback(receber); mqtt.setBufferSize(512); mqtt.setSocketTimeout(2);
 WiFi.begin("Wokwi-GUEST","",6); leitura=millis();
 Serial.println("HGE iniciado. Teste local: 1 OFF ou 1 ON (circuitos 1 a 3).");
}
void loop(){
 medir();
 if(WiFi.status()==WL_CONNECTED && !mqtt.connected() && millis()-tentativa>=5000){
  tentativa=millis();
  String cliente="hge-esp32-"+String(esp_random(),HEX);
  if(mqtt.connect(cliente.c_str())){
   mqtt.subscribe(topicoComando.c_str()); Serial.println("MQTT conectado."); publicar();
  }
 }
 mqtt.loop();
 while(Serial.available()){
  char c=Serial.read();
  if(c=='\n'){
   entrada.trim();
   for(int i=0;i<3;i++){
    if(entrada==String(i+1)+" ON") acionar(i,true);
    else if(entrada==String(i+1)+" OFF") acionar(i,false);
   }
   entrada="";
  }else if(entrada.length()<32) entrada+=c;
 }
 if(millis()-envio>=2000) publicar();
 delay(10);
}
