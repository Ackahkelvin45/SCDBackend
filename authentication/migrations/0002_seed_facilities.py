from django.db import migrations

# Facility list supplied by the project owner, 4 Nov 2026. Edit or add facilities in Django admin afterwards.
FACILITIES = {
    "Greater Accra": [
        "Korle Bu Teaching Hospital", "Greater Accra Regional Hospital (Ridge)",
        "University of Ghana Medical Centre (UGMC)", "37 Military Hospital", "Tema General Hospital",
    ],
    "Ashanti": [
        "Komfo Anokye Teaching Hospital", "Kumasi South Hospital", "Suntreso Government Hospital",
        "Presbyterian Hospital, Agogo", "Manhyia District Hospital",
    ],
    "Western": [
        "Effia Nkwanta Regional Hospital", "Tarkwa Municipal Hospital", "Axim Government Hospital",
        "Prestea Government Hospital", "Father Thomas Alan Rooney Memorial Hospital",
    ],
    "Central": [
        "Cape Coast Teaching Hospital", "Winneba Trauma and Specialist Hospital",
        "St. Francis Xavier Hospital, Assin Fosu", "Our Lady of Grace Hospital, Breman Asikuma",
        "Saltpond Municipal Hospital",
    ],
    "Eastern": [
        "Eastern Regional Hospital, Koforidua", "St. Dominic Hospital, Akwatia", "Holy Family Hospital, Nkawkaw",
        "Atua Government Hospital", "Donkorkrom Presbyterian Hospital",
    ],
    "Volta": [
        "Ho Teaching Hospital", "Battor Catholic Hospital", "Margaret Marquart Catholic Hospital",
        "Sacred Heart Hospital, Abor", "St. Anthony's Hospital, Dzodze",
    ],
    "Northern": [
        "Tamale Teaching Hospital", "Tamale West Hospital", "SDA Hospital, Tamale", "Yendi Municipal Hospital",
        "Savelugu Municipal Hospital",
    ],
    "Upper East": [
        "Upper East Regional Hospital, Bolgatanga", "War Memorial Hospital, Navrongo", "Presbyterian Hospital, Bawku",
        "Sandema District Hospital", "Zebilla District Hospital",
    ],
    "Upper West": [
        "Upper West Regional Hospital, Wa", "St. Joseph's Hospital, Jirapa", "St. Theresa's Hospital, Nandom",
        "Tumu Municipal Hospital", "Lawra Municipal Hospital",
    ],
    "Bono": [
        "Sunyani Teaching Hospital", "Dormaa Presbyterian Hospital", "Berekum Holy Family Hospital",
        "Wenchi Methodist Hospital", "Sampa Government Hospital",
    ],
    "Bono East": [
        "Holy Family Hospital, Techiman", "Mathias Catholic Hospital, Yeji", "Kintampo Municipal Hospital",
        "Atebubu Government Hospital", "St. Theresa's Hospital, Nkoranza",
    ],
    "Ahafo": [
        "Goaso Municipal Hospital", "St. Elizabeth Hospital, Hwidiem", "Kenyasi Government Hospital",
        "Mim Government Hospital", "Kukuom District Hospital",
    ],
    "Savannah": [
        "Bole Municipal Hospital", "Damongo Municipal Hospital", "Salaga District Hospital",
    ],
    "North East": [
        "Baptist Medical Centre, Nalerigu", "Walewale Municipal Hospital", "Nalerigu Municipal Hospital",
        "Gambaga District Hospital", "Bunkpurugu District Hospital",
    ],
    "Oti": [
        "Worawora Government Hospital", "Comboni Hospital, Sogakope", "St. Joseph's Catholic Hospital",
        "St. Mary Theresa Catholic Hospital", "Jasikan District Hospital",
    ],
    "Western North": [
        "Sefwi-Wiawso Government Hospital", "Bibiani Government Hospital", "St. John of God Hospital",
        "Juaboso District Hospital", "Dadieso Government Hospital",
    ],
}


def seed(apps, schema_editor):
    Facility = apps.get_model("authentication", "Facility")
    for region, names in FACILITIES.items():
        for name in names:
            Facility.objects.get_or_create(name=name, defaults={"region": region})


class Migration(migrations.Migration):
    dependencies = [("authentication", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
