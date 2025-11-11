import requests
from bs4 import BeautifulSoup
import json


response = requests.get('https://www.studentenwerk-magdeburg.de/mensen-cafeterien/mensa-unicampus-speiseplan-unten/')
response.raise_for_status()  # Ensure we got a successful response

soup = BeautifulSoup(response.text, 'html.parser')
    
def check_veg_nonVeg(category_text):
    veg_keywords = ['vegetarisch', 'vegan']
    non_veg_keywords = ['rind', 'schwein', 'geflügel', 'fisch', 'hähnchen', 'lamm', 'suppe']

    category_text_lower = category_text.lower()
    if any(keyword in category_text_lower for keyword in veg_keywords):
        return 'Vegetarian/Vegan'
    elif any(keyword in category_text_lower for keyword in non_veg_keywords):
        return 'Non-Vegetarian'
    else:
        return category_text
speisekarte = []

    # Assuming the menu items are contained in elements with class 'menu-item'
mensa_div = soup.find('div',class_='mensa')
all_tables = mensa_div.find_all('table')
print(f"Found {len(all_tables)} separate tables (days) on the menu.\n")
for table in all_tables:
    date = table.find('thead').get_text().strip()
    day_menu={
        'date': date,
        'meals': []
    }

    meals = table.find('tbody').find_all('tr')
    for meal in meals:
        columns = meal.find_all('td')
        meal_obj_german = columns[0].find('span', class_='gruen')
        if meal_obj_german:
            meal_name_german = meal_obj_german.get_text().strip()
        else:
            meal_name_german = columns[0].find(string=True, recursive=False).strip()
        meal_obj_english = columns[0].find('span', class_='grau')
        if meal_obj_english:
            meal_name_english = meal_obj_english.get_text().strip()
        else:
            meal_name_english = "No English name available"
        mensa_price = columns[0].find('span', class_='mensapreis').get_text().strip()


        img_obj = columns[1].find('img')
        if img_obj:
            img_alt = img_obj.get('alt', '').strip() #get the alt text for category

            category = check_veg_nonVeg(img_alt)# check if veg or non-veg
            #store meal info
        meal_info = {
            'name_german': meal_name_german,
            'name_english': meal_name_english,
            'price': mensa_price,
            'category': category
        }
        day_menu['meals'].append(meal_info) #add meal to day's menu
    speisekarte.append(day_menu) #add day's menu to speisekarte

    output_filename = 'mensa_unicampus_speisekarte.json'
with open(output_filename, 'w', encoding='utf-8') as f:
    json.dump(speisekarte, f, ensure_ascii=False, indent=4)
print(f"\nSuccess! Saved the mensa speisekarte to '{output_filename}'.")

        
      
 
        

    
