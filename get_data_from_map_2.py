import requests
import json

bbox = "52.128852,11.628191,52.150147,11.677952"

query = f"""
[out:json][timeout:25];
(
  // Restaurants, Kinos, Apotheken, Cafes
  node["amenity"~"restaurant|cinema|pharmacy|cafe|kiosk |spätshop"]({bbox});
  way["amenity"~"restaurant|cinema|pharmacy|cafe|kiosk|spätshop"]({bbox});

  // Supermaerkte, Kiosks
  node["shop"~"supermarket|kiosk"]({bbox});
  way["shop"~"supermarket|kiosk"]({bbox});
);
out center;
"""


overpass_url = "https://overpass.kumi.systems/api/interpreter" # Alternative: "https://overpass-api.de/api/interpreter"
try:
    response = requests.post(overpass_url, data={'data': query})
    response.raise_for_status() # Raises an error for bad responses (like 404, 500)
    data = response.json()
except requests.exceptions.RequestException as e:
    print(f"--- API Request Failed ---")
    print(f"Error: {e}")
    print("Could not connect to Overpass API. Check your internet or try again later.")
    exit()

print(f"Found {len(data['elements'])} total elements from OpenStreetMap.")


all_places = []
for place in data['elements']:
    tags = place.get('tags', {})
    
    # Determine our category name
    # (This is just for our own 'category' field)
    category = tags.get('amenity')   # e.g., 'restaurant'
    if not category:
        category = tags.get('shop') # e.g., 'supermarket'

    # Get coordinates
    # 'out center;' is great, it gives us a single point
    # for both nodes (points) and ways (buildings).
    if 'center' in place:
        lat = place['center']['lat']
        lon = place['center']['lon']
    else:
        # It's a regular node
        lat = place.get('lat')
        lon = place.get('lon')

    # Skip if we couldn't get a location
    if not lat or not lon:
        continue

    # Build our clean object
    place_object = {
        "name": tags.get('name', 'Name not available'),
        "category": category or "unknown",
        "gps": {
            "latitude": lat,
            "longitude": lon
        },
        "osm_id": place['id'] # Good to keep for reference
    }
    all_places.append(place_object)


output_filename = 'osm_my_area3.json' # Changed the name slightly
with open(output_filename, 'w', encoding='utf-8') as f:
    json.dump(all_places, f, ensure_ascii=False, indent=2)

print(f"\nSuccess! Saved {len(all_places)} places to '{output_filename}'.")





