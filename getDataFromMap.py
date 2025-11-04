import geopandas as gpd
import json

CodsToFind = {
    '2081': 'Restaurant',
    '1230': 'Supermarkt',
    '2055': 'kiosk',
   # 2056: 'Apotheke',
   #
}  # dict building of codes to find

filePath = "sie05_f.shp"
print(f"Loading {filePath} ... (This may take a moment)")

try:
    gdfforAllBuildings = gpd.read_file(filePath)
    print("File loaded. Here are the columns found:")
    print(gdfforAllBuildings.columns)
    filterColumn = 'GFK'  # Column to filter on changed from 'gebaeudefunktion' to 'GFK'
    nameColumn = 'NAM'  # Adjust if necessary changed NAME' to 'NAM'
    print(f"Info: The '{filterColumn}' column has this data type: {gdfforAllBuildings[filterColumn].dtype}")
    print(f"\nFiltering for items in the '{filterColumn}' column...")
    gdfFiltered = gdfforAllBuildings[gdfforAllBuildings[filterColumn].isin(CodsToFind.keys())]
    print(f"Filtered data to {len(gdfFiltered)} entries based on {filterColumn}.")

    allPlaces = []
    for index, row in gdfFiltered.iterrows():
        atkis_code = row[filterColumn]
        our_category = FUNCTION_CODES_TO_FIND[atkis_code]

        
        place_object = {
            "name": row.get(nameColumn) or "Name not available", 
            "category": our_category,
            "atkis_code": atkis_code,
            "gps": {
                "latitude": row.geometry.y,
                "longitude": row.geometry.x
            }
        }
        allPlaces.append(place_object)

    output_filename = 'magdeburg_pois.json'
    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(allPlaces, f, ensure_ascii=False, indent=2)

    print(f"\nSuccess! Saved all {len(allPlaces)} places to '{output_filename}'.")

except Exception as e:
    print(f"\n--- An Error Occurred ---")
    print(f"Error: {e}")
    print("\n--- Troubleshooting ---")
    print(f"1. Did you put the '{filePath}' in the same folder?")
    print("2. Is the filter column name correct? We used 'gebaeudefunktion'.")
    print("3. Is the name column correct? We used 'NAME'.")
    print("Check the column list printed above and adjust the script if needed.")
