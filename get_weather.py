import urllib.request
import json
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

url = "https://wttr.in/Changchun?format=j1"
data = urllib.request.urlopen(url).read().decode()
d = json.loads(data)

cc = d['current_condition'][0]
print("长春今天天气：")
print("温度: " + cc['temp_C'] + "°C")
print("体感温度: " + cc['FeelsLikeC'] + "°C")
print("天气: " + cc['weatherDesc'][0]['value'])
print("湿度: " + cc['humidity'] + "%")
print("风速: " + cc['windspeedKmph'] + " km/h")
print("能见度: " + cc['visibility'] + " km")