import requests
from bs4 import BeautifulSoup
from main import url

def  crawl(url):
    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
     response = requests.get(url, headers=headers)
     if response.status_code == 200:
      soup = BeautifulSoup(response.text, "html.parser")
      return soup
     else:
         print(f"Hata: Sayafaya ulaşılamadı. Status Code: {response.status_code}")
         return None
    except Exception as e:
        print(f"Bir hata oluştu: {e}")
        return None 
     