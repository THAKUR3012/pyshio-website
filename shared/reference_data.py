"""Fixed reference lists: Indian states, care areas, the condition (diagnosis) list and medicine care areas.

Conditions use ICD-10 codes so analytics line up with national reporting. Extend freely; codes must stay unique.
"""



INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya",
    "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal",
    # Union territories
    "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi",
    "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
]

# Used to fill in the state for records saved before states were collected (and as a default for new ones)
CITY_STATE = {
    "mumbai": "Maharashtra", "navi mumbai": "Maharashtra", "thane": "Maharashtra", "pune": "Maharashtra",
    "nagpur": "Maharashtra", "nashik": "Maharashtra", "aurangabad": "Maharashtra", "kolhapur": "Maharashtra",
    "bengaluru": "Karnataka", "bangalore": "Karnataka", "mysuru": "Karnataka", "mysore": "Karnataka",
    "delhi": "Delhi", "new delhi": "Delhi", "noida": "Uttar Pradesh", "ghaziabad": "Uttar Pradesh",
    "lucknow": "Uttar Pradesh", "kanpur": "Uttar Pradesh", "varanasi": "Uttar Pradesh",
    "gurugram": "Haryana", "gurgaon": "Haryana", "faridabad": "Haryana", "chandigarh": "Chandigarh",
    "chennai": "Tamil Nadu", "coimbatore": "Tamil Nadu", "madurai": "Tamil Nadu",
    "hyderabad": "Telangana", "secunderabad": "Telangana", "visakhapatnam": "Andhra Pradesh",
    "vijayawada": "Andhra Pradesh", "kolkata": "West Bengal", "howrah": "West Bengal",
    "ahmedabad": "Gujarat", "surat": "Gujarat", "vadodara": "Gujarat", "rajkot": "Gujarat",
    "jaipur": "Rajasthan", "jodhpur": "Rajasthan", "udaipur": "Rajasthan",
    "indore": "Madhya Pradesh", "bhopal": "Madhya Pradesh", "patna": "Bihar", "ranchi": "Jharkhand",
    "bhubaneswar": "Odisha", "kochi": "Kerala", "thiruvananthapuram": "Kerala", "kozhikode": "Kerala",
    "panaji": "Goa", "goa": "Goa", "guwahati": "Assam", "dehradun": "Uttarakhand", "ludhiana": "Punjab",
    "amritsar": "Punjab", "raipur": "Chhattisgarh", "srinagar": "Jammu and Kashmir", "jammu": "Jammu and Kashmir",
    "puducherry": "Puducherry",
}


def state_for_city(city) -> str:
    return CITY_STATE.get((city or "").strip().lower())


def normalise_state(value):
    """Match a state name case-insensitively (OpenStreetMap and people spell them slightly differently)."""
    text = (value or "").strip().lower().replace("&", "and")
    if not text:
        return None
    for state in INDIAN_STATES:
        if state.lower() == text:
            return state
    aliases = {"nct of delhi": "Delhi", "national capital territory of delhi": "Delhi", "orissa": "Odisha",
               "pondicherry": "Puducherry", "uttaranchal": "Uttarakhand"}
    return aliases.get(text)


CARE_AREAS = [
    "Cardiology", "Diabetes & Endocrine", "Respiratory", "Gastro & Liver", "Infections", "Pain & Ortho",
    "Neurology", "Psychiatry", "Dermatology", "Women's health", "Paediatrics", "ENT & Eye", "Dental",
    "Kidney & Urology", "General",
]

# (ICD-10 code, name, care area)
CONDITIONS = [
    # Cardiology
    ("I10", "Hypertension (high blood pressure)", "Cardiology"),
    ("I25.1", "Coronary artery disease", "Cardiology"),
    ("I20.9", "Angina", "Cardiology"),
    ("I50.9", "Heart failure", "Cardiology"),
    ("I48.9", "Atrial fibrillation", "Cardiology"),
    ("E78.5", "Dyslipidaemia (high cholesterol)", "Cardiology"),
    ("I73.9", "Peripheral vascular disease", "Cardiology"),
    # Diabetes & Endocrine
    ("E11.9", "Type 2 diabetes", "Diabetes & Endocrine"),
    ("E10.9", "Type 1 diabetes", "Diabetes & Endocrine"),
    ("R73.03", "Prediabetes", "Diabetes & Endocrine"),
    ("E03.9", "Hypothyroidism", "Diabetes & Endocrine"),
    ("E05.9", "Hyperthyroidism", "Diabetes & Endocrine"),
    ("E66.9", "Obesity", "Diabetes & Endocrine"),
    ("E55.9", "Vitamin D deficiency", "Diabetes & Endocrine"),
    # Respiratory
    ("J45.9", "Asthma", "Respiratory"),
    ("J44.9", "COPD", "Respiratory"),
    ("J06.9", "Upper respiratory tract infection (cold)", "Respiratory"),
    ("J18.9", "Pneumonia", "Respiratory"),
    ("J20.9", "Acute bronchitis", "Respiratory"),
    ("J30.4", "Allergic rhinitis", "Respiratory"),
    ("R05", "Cough", "Respiratory"),
    # Gastro & Liver
    ("K21.9", "Acid reflux (GERD)", "Gastro & Liver"),
    ("K29.7", "Gastritis", "Gastro & Liver"),
    ("K30", "Dyspepsia (indigestion)", "Gastro & Liver"),
    ("K58.9", "Irritable bowel syndrome", "Gastro & Liver"),
    ("K59.0", "Constipation", "Gastro & Liver"),
    ("K76.0", "Fatty liver", "Gastro & Liver"),
    ("K80.2", "Gallstones", "Gastro & Liver"),
    ("K64.9", "Haemorrhoids (piles)", "Gastro & Liver"),
    ("K27.9", "Peptic ulcer", "Gastro & Liver"),
    # Infections
    ("A01.0", "Typhoid", "Infections"),
    ("A90", "Dengue", "Infections"),
    ("B54", "Malaria", "Infections"),
    ("A92.0", "Chikungunya", "Infections"),
    ("B34.9", "Viral fever", "Infections"),
    ("A09", "Gastroenteritis / infectious diarrhoea", "Infections"),
    ("A15.9", "Tuberculosis", "Infections"),
    ("U07.1", "COVID-19", "Infections"),
    ("B19.9", "Viral hepatitis", "Infections"),
    # Pain & Ortho
    ("M54.5", "Low back pain", "Pain & Ortho"),
    ("M54.2", "Neck pain", "Pain & Ortho"),
    ("M17.9", "Knee osteoarthritis", "Pain & Ortho"),
    ("M06.9", "Rheumatoid arthritis", "Pain & Ortho"),
    ("M81.0", "Osteoporosis", "Pain & Ortho"),
    ("M75.0", "Frozen shoulder", "Pain & Ortho"),
    ("M10.9", "Gout", "Pain & Ortho"),
    ("S93.4", "Ankle sprain", "Pain & Ortho"),
    ("M79.1", "Muscle pain (myalgia)", "Pain & Ortho"),
    ("G56.0", "Carpal tunnel syndrome", "Pain & Ortho"),
    # Neurology
    ("G43.9", "Migraine", "Neurology"),
    ("G44.2", "Tension headache", "Neurology"),
    ("G40.9", "Epilepsy", "Neurology"),
    ("I63.9", "Stroke", "Neurology"),
    ("G20", "Parkinson's disease", "Neurology"),
    ("G62.9", "Peripheral neuropathy", "Neurology"),
    ("R42", "Vertigo / dizziness", "Neurology"),
    # Psychiatry
    ("F32.9", "Depression", "Psychiatry"),
    ("F41.1", "Generalised anxiety disorder", "Psychiatry"),
    ("F41.0", "Panic disorder", "Psychiatry"),
    ("F51.0", "Insomnia", "Psychiatry"),
    ("F90.0", "ADHD", "Psychiatry"),
    ("F31.9", "Bipolar disorder", "Psychiatry"),
    ("F20.9", "Schizophrenia", "Psychiatry"),
    ("F10.2", "Alcohol dependence", "Psychiatry"),
    # Dermatology
    ("L70.0", "Acne", "Dermatology"),
    ("L20.9", "Eczema (atopic dermatitis)", "Dermatology"),
    ("L40.0", "Psoriasis", "Dermatology"),
    ("B35.4", "Fungal skin infection (tinea)", "Dermatology"),
    ("L50.9", "Urticaria (hives)", "Dermatology"),
    ("L80", "Vitiligo", "Dermatology"),
    ("L65.9", "Hair loss", "Dermatology"),
    ("L03.9", "Cellulitis", "Dermatology"),
    ("B86", "Scabies", "Dermatology"),
    # Women's health
    ("E28.2", "PCOS", "Women's health"),
    ("N94.6", "Painful periods (dysmenorrhoea)", "Women's health"),
    ("N92.0", "Heavy menstrual bleeding", "Women's health"),
    ("Z34.9", "Pregnancy care", "Women's health"),
    ("N95.1", "Menopause symptoms", "Women's health"),
    ("N76.0", "Vaginitis", "Women's health"),
    # Paediatrics
    ("Z00.1", "Child health check-up", "Paediatrics"),
    ("P59.9", "Newborn jaundice", "Paediatrics"),
    ("J21.9", "Bronchiolitis", "Paediatrics"),
    ("B01.9", "Chickenpox", "Paediatrics"),
    ("R62.5", "Poor growth / weight gain", "Paediatrics"),
    ("L22", "Nappy rash", "Paediatrics"),
    # ENT & Eye
    ("H66.9", "Ear infection (otitis media)", "ENT & Eye"),
    ("J02.9", "Sore throat (pharyngitis)", "ENT & Eye"),
    ("J03.9", "Tonsillitis", "ENT & Eye"),
    ("J32.9", "Sinusitis", "ENT & Eye"),
    ("H10.9", "Conjunctivitis", "ENT & Eye"),
    ("H25.9", "Cataract", "ENT & Eye"),
    ("H40.9", "Glaucoma", "ENT & Eye"),
    ("H04.1", "Dry eye", "ENT & Eye"),
    # Dental
    ("K02.9", "Dental caries (cavity)", "Dental"),
    ("K05.1", "Gingivitis", "Dental"),
    ("K04.0", "Pulpitis (tooth nerve inflammation)", "Dental"),
    ("K05.3", "Periodontitis", "Dental"),
    ("K08.8", "Toothache", "Dental"),
    # Kidney & Urology
    ("N39.0", "Urinary tract infection", "Kidney & Urology"),
    ("N18.9", "Chronic kidney disease", "Kidney & Urology"),
    ("N20.0", "Kidney stones", "Kidney & Urology"),
    ("N40", "Enlarged prostate (BPH)", "Kidney & Urology"),
    # General
    ("D50.9", "Iron deficiency anaemia", "General"),
    ("E53.8", "Vitamin B12 deficiency", "General"),
    ("R50.9", "Fever", "General"),
    ("R53.8", "Weakness / fatigue", "General"),
    ("T78.4", "Allergy", "General"),
    ("Z00.0", "General health check-up", "General"),
]

# Care area of a medicine, by its generic (salt) name. Used for the shared catalogue and MR division filters.
GENERIC_CARE_AREA = {
    "paracetamol": "General", "ibuprofen": "Pain & Ortho", "diclofenac": "Pain & Ortho", "aceclofenac": "Pain & Ortho",
    "pregabalin": "Neurology", "pantoprazole": "Gastro & Liver", "omeprazole": "Gastro & Liver",
    "rabeprazole": "Gastro & Liver", "ondansetron": "Gastro & Liver", "domperidone": "Gastro & Liver",
    "ursodeoxycholic acid": "Gastro & Liver", "azithromycin": "Infections", "amoxicillin": "Infections",
    "amoxicillin + clavulanic acid": "Infections", "cefixime": "Infections", "doxycycline": "Infections",
    "nitrofurantoin": "Kidney & Urology", "tamsulosin": "Kidney & Urology",
    "cetirizine": "Respiratory", "levocetirizine": "Respiratory", "fexofenadine": "Respiratory",
    "montelukast": "Respiratory", "salbutamol": "Respiratory", "budesonide + formoterol": "Respiratory",
    "metformin": "Diabetes & Endocrine", "glimepiride": "Diabetes & Endocrine", "sitagliptin": "Diabetes & Endocrine",
    "vildagliptin": "Diabetes & Endocrine", "levothyroxine": "Diabetes & Endocrine",
    "cholecalciferol": "Diabetes & Endocrine",
    "telmisartan": "Cardiology", "amlodipine": "Cardiology", "atorvastatin": "Cardiology",
    "rosuvastatin": "Cardiology", "aspirin": "Cardiology", "metoprolol": "Cardiology", "clopidogrel": "Cardiology",
    "escitalopram": "Psychiatry", "sertraline": "Psychiatry", "clonazepam": "Psychiatry",
    "isotretinoin": "Dermatology", "clindamycin": "Dermatology", "terbinafine": "Dermatology",
    "luliconazole": "Dermatology", "ferrous ascorbate": "General", "methylcobalamin": "General",
    "folic acid": "Women's health", "progesterone": "Women's health",
    "chlorhexidine": "Dental", "moxifloxacin": "ENT & Eye", "carboxymethylcellulose": "ENT & Eye",
}


def care_area_for(generic_name):
    return GENERIC_CARE_AREA.get((generic_name or "").strip().lower())


AGE_BANDS = [("0-12", 0, 12), ("13-17", 13, 17), ("18-29", 18, 29), ("30-44", 30, 44), ("45-59", 45, 59),
             ("60+", 60, 200)]


# Common lab tests: (name, code, category, sample, fasting, typical MRP ₹, unit, ref low, ref high).
# Ranges are typical adult values for showing high/low flags; the lab's own report is what counts.
LAB_TESTS = [
    ("Complete Blood Count (CBC)", "CBC", "Blood", "Blood", 0, 350, None, None, None),
    ("Haemoglobin", "HB", "Blood", "Blood", 0, 150, "g/dL", 12.0, 17.0),
    ("HbA1c (glycated haemoglobin)", "HBA1C", "Blood", "Blood", 0, 550, "%", 4.0, 5.6),
    ("Fasting Blood Sugar (FBS)", "FBS", "Blood", "Blood", 1, 100, "mg/dL", 70, 100),
    ("Post-prandial Blood Sugar (PPBS)", "PPBS", "Blood", "Blood", 0, 100, "mg/dL", 70, 140),
    ("Random Blood Sugar", "RBS", "Blood", "Blood", 0, 90, "mg/dL", 70, 140),
    ("Lipid Profile", "LIPID", "Blood", "Blood", 1, 650, None, None, None),
    ("Total Cholesterol", "CHOL", "Blood", "Blood", 1, 200, "mg/dL", 0, 200),
    ("LDL Cholesterol", "LDL", "Blood", "Blood", 1, 250, "mg/dL", 0, 100),
    ("Triglycerides", "TG", "Blood", "Blood", 1, 220, "mg/dL", 0, 150),
    ("Liver Function Test (LFT)", "LFT", "Blood", "Blood", 0, 700, None, None, None),
    ("SGPT (ALT)", "ALT", "Blood", "Blood", 0, 200, "U/L", 7, 56),
    ("Kidney Function Test (KFT)", "KFT", "Blood", "Blood", 0, 750, None, None, None),
    ("Serum Creatinine", "CREAT", "Blood", "Blood", 0, 200, "mg/dL", 0.6, 1.3),
    ("Blood Urea", "UREA", "Blood", "Blood", 0, 180, "mg/dL", 15, 40),
    ("Uric Acid", "URIC", "Blood", "Blood", 0, 250, "mg/dL", 3.5, 7.2),
    ("Thyroid Profile (T3, T4, TSH)", "THY", "Blood", "Blood", 0, 600, None, None, None),
    ("TSH", "TSH", "Blood", "Blood", 0, 350, "µIU/mL", 0.4, 4.0),
    ("Vitamin D (25-OH)", "VITD", "Blood", "Blood", 0, 1400, "ng/mL", 30, 100),
    ("Vitamin B12", "B12", "Blood", "Blood", 0, 1000, "pg/mL", 200, 900),
    ("Serum Iron Studies", "IRON", "Blood", "Blood", 0, 900, None, None, None),
    ("ESR", "ESR", "Blood", "Blood", 0, 120, "mm/hr", 0, 20),
    ("C-Reactive Protein (CRP)", "CRP", "Blood", "Blood", 0, 500, "mg/L", 0, 6),
    ("Dengue NS1 Antigen", "NS1", "Blood", "Blood", 0, 600, None, None, None),
    ("Dengue IgM / IgG", "DENIG", "Blood", "Blood", 0, 900, None, None, None),
    ("Malaria Antigen (rapid)", "MAL", "Blood", "Blood", 0, 400, None, None, None),
    ("Widal Test", "WIDAL", "Blood", "Blood", 0, 250, None, None, None),
    ("Platelet Count", "PLT", "Blood", "Blood", 0, 150, "lakh/µL", 1.5, 4.5),
    ("Urine Routine & Microscopy", "URINE", "Urine", "Urine", 0, 150, None, None, None),
    ("Urine Culture & Sensitivity", "UCS", "Urine", "Urine", 0, 800, None, None, None),
    ("Stool Routine", "STOOL", "Other", "Stool", 0, 200, None, None, None),
    ("PSA (prostate)", "PSA", "Blood", "Blood", 0, 800, "ng/mL", 0, 4),
    ("Beta hCG (pregnancy)", "BHCG", "Blood", "Blood", 0, 700, None, None, None),
    ("ECG", "ECG", "Cardiac", None, 0, 300, None, None, None),
    ("2D Echo", "ECHO", "Cardiac", None, 0, 2500, None, None, None),
    ("Chest X-ray (PA view)", "CXR", "Imaging", None, 0, 450, None, None, None),
    ("X-ray Knee (AP/Lat)", "XKNEE", "Imaging", None, 0, 600, None, None, None),
    ("X-ray Lumbar Spine", "XLS", "Imaging", None, 0, 700, None, None, None),
    ("Ultrasound Abdomen", "USGA", "Imaging", None, 1, 1500, None, None, None),
    ("Ultrasound Pelvis", "USGP", "Imaging", None, 0, 1400, None, None, None),
    ("MRI Lumbar Spine", "MRILS", "Imaging", None, 0, 6500, None, None, None),
    ("Bone Mineral Density (DEXA)", "DEXA", "Imaging", None, 0, 2500, None, None, None),
]

# Tests doctors usually order for a condition (used for demo data and as quick picks in the writer)
CONDITION_TESTS = {
    "E11.9": ["HbA1c (glycated haemoglobin)", "Fasting Blood Sugar (FBS)", "Post-prandial Blood Sugar (PPBS)"],
    "R73.03": ["HbA1c (glycated haemoglobin)", "Fasting Blood Sugar (FBS)"],
    "I10": ["Lipid Profile", "Serum Creatinine", "ECG"],
    "E78.5": ["Lipid Profile"],
    "I25.1": ["ECG", "2D Echo", "Lipid Profile"],
    "E03.9": ["TSH"], "E05.9": ["Thyroid Profile (T3, T4, TSH)"],
    "A90": ["Dengue NS1 Antigen", "Complete Blood Count (CBC)", "Platelet Count"],
    "B54": ["Malaria Antigen (rapid)", "Complete Blood Count (CBC)"],
    "A01.0": ["Widal Test", "Complete Blood Count (CBC)"],
    "B34.9": ["Complete Blood Count (CBC)"], "R50.9": ["Complete Blood Count (CBC)", "C-Reactive Protein (CRP)"],
    "N39.0": ["Urine Routine & Microscopy", "Urine Culture & Sensitivity"],
    "M17.9": ["X-ray Knee (AP/Lat)"], "M54.5": ["X-ray Lumbar Spine"], "M81.0": ["Bone Mineral Density (DEXA)"],
    "D50.9": ["Haemoglobin", "Serum Iron Studies"], "E53.8": ["Vitamin B12"], "E55.9": ["Vitamin D (25-OH)"],
    "J18.9": ["Chest X-ray (PA view)", "Complete Blood Count (CBC)"], "K76.0": ["Liver Function Test (LFT)", "Ultrasound Abdomen"],
    "N18.9": ["Kidney Function Test (KFT)"], "M10.9": ["Uric Acid"], "N40": ["PSA (prostate)"],
    "Z34.9": ["Beta hCG (pregnancy)", "Ultrasound Pelvis"], "R53.8": ["Complete Blood Count (CBC)", "TSH", "Vitamin B12"],
}
