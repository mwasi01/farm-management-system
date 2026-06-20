from app import app, db
from app import LivestockCategory, Breed, CropCategory, Crop

with app.app_context():
    breeds_data = {
        'Cattle (Ng\'ombe)': ['Friesian','Jersey','Ayrshire','Guernsey','Boran','Zebu','Angus','Hereford','Charolais','Simmental','Sahiwal'],
        'Goats (Mbuzi)': ['Toggenburg','Saanen','Galla','East African','Boer','Angora','Nubian','Alpine'],
        'Sheep (Kondoo)': ['Merino','Corriedale','Dorper','Red Maasai','Blackhead Persian','Suffolk'],
        'Poultry (Kuku)': ['Layer (Hybrid)','Broiler (Hybrid)','Kienyeji','Rhode Island Red','Leghorn','Australorp','Sussex','Kuroiler','Kenbro'],
        'Pigs (Nguruwe)': ['Large White','Landrace','Hampshire','Duroc','Berkshire','Tamworth','Pietrain'],
        'Bees (Nyuki)': ['Apis mellifera (Italian)','Apis mellifera (Carniolan)','Apis mellifera (African)'],
        'Rabbits (Sungura)': ['New Zealand White','California','Chinchilla','Flemish Giant','Rex','Angora'],
        'Fish (Samaki)': ['Tilapia (Nile)','Catfish (African)','Trout (Rainbow)','Mudfish','Lungfish'],
        'Donkeys (Punda)': ['Somalia Donkey','Masai Donkey','American Mammoth Jackstock']
    }
    for cat_name, breeds in breeds_data.items():
        cat = LivestockCategory.query.filter_by(name=cat_name).first()
        if not cat:
            cat = LivestockCategory(name=cat_name)
            db.session.add(cat)
            db.session.flush()
        for b in breeds:
            if not Breed.query.filter_by(name=b, category_id=cat.id).first():
                db.session.add(Breed(category_id=cat.id, name=b))

    crops_data = {
        'Cereals': [('Maize','H614',120,'Long Rains','75x25 cm',25,8), ('Wheat','Kenya King',100,'Long Rains','15 cm spacing',20,10)],
        'Legumes': [('Beans','Rosecoco',90,'Long Rains','50x15 cm',5,40), ('Peas','Green Feast',70,'Long Rains','45x10 cm',4,50)],
        'Vegetables': [('Tomatoes','Anna F1',90,'All Year','60x45 cm',30,20), ('Cabbage','Gloria F1',80,'All Year','60x45 cm',20,15), ('Kales','Sukuma wiki',70,'All Year','45x30 cm',10,10)],
        'Fruits': [('Mangoes','Apple Mango',120,'Long Rains','10x10 m',800,0), ('Bananas','Giant Cavendish',365,'All Year','3x3 m',500,0)],
        'Root Crops': [('Potatoes','Shangi',90,'Long Rains','30x30 cm',25,300), ('Cassava','TMS 60444',365,'Long Rains','1x1 m',40,0)]
    }
    for cat_name, crops in crops_data.items():
        cat = CropCategory.query.filter_by(name=cat_name).first()
        if not cat:
            cat = CropCategory(name=cat_name)
            db.session.add(cat)
            db.session.flush()
        for name, variety, growth, season, spacing, yield_per_acre, seed_rate in crops:
            if not Crop.query.filter_by(name=name, variety=variety).first():
                db.session.add(Crop(
                    name=name,
                    category_id=cat.id,
                    variety=variety,
                    growth_period_days=growth,
                    planting_season=season,
                    spacing=spacing,
                    expected_yield_per_acre=yield_per_acre,
                    seed_rate_kg_per_acre=seed_rate
                ))
    db.session.commit()
    print('✅ Data seeded successfully')
