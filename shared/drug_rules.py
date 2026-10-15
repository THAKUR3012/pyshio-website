"""Curated prescription safety rules (advisory; the doctor decides).

Salts are written the way shared/salts.py normalises them (lower case, aliases applied). A rule names either a
salt ("warfarin") or a class ("class:nsaid"). Keep the list short and high-signal: only what a busy prescriber in
India would want flagged. Admins can extend it in code; every message tells the doctor to verify in a clinical reference.
"""

CLASSES = {
    "nsaid": ["ibuprofen", "diclofenac", "aceclofenac", "naproxen", "mefenamic acid", "ketorolac", "etoricoxib",
              "piroxicam", "indomethacin", "nimesulide", "celecoxib", "aspirin"],
    "anticoagulant": ["warfarin", "acenocoumarol", "apixaban", "rivaroxaban", "dabigatran"],
    "antiplatelet": ["clopidogrel", "aspirin", "ticagrelor", "prasugrel"],
    "ssri": ["sertraline", "fluoxetine", "escitalopram", "citalopram", "paroxetine", "fluvoxamine"],
    "snri": ["venlafaxine", "duloxetine", "desvenlafaxine"],
    "macrolide": ["clarithromycin", "erythromycin", "azithromycin"],
    "strong_cyp3a4_inhibitor": ["clarithromycin", "erythromycin", "ketoconazole", "itraconazole", "ritonavir", "voriconazole"],
    "statin_cyp3a4": ["simvastatin", "atorvastatin", "lovastatin"],
    "statin": ["atorvastatin", "rosuvastatin", "simvastatin", "pravastatin", "pitavastatin", "lovastatin"],
    "ace_inhibitor": ["enalapril", "ramipril", "lisinopril", "perindopril", "captopril"],
    "arb": ["telmisartan", "losartan", "olmesartan", "valsartan", "irbesartan", "candesartan"],
    "k_sparing": ["spironolactone", "eplerenone", "amiloride", "triamterene"],
    "potassium": ["potassium chloride", "potassium citrate"],
    "qt_prolonging": ["azithromycin", "clarithromycin", "erythromycin", "levofloxacin", "moxifloxacin", "ciprofloxacin",
                      "ondansetron", "domperidone", "haloperidol", "hydroxychloroquine", "chloroquine", "amiodarone",
                      "citalopram", "escitalopram", "quetiapine"],
    "fluoroquinolone": ["ciprofloxacin", "levofloxacin", "ofloxacin", "moxifloxacin", "norfloxacin"],
    "tetracycline": ["doxycycline", "tetracycline", "minocycline"],
    "sulfonylurea": ["glimepiride", "gliclazide", "glipizide", "glyburide"],
    "benzodiazepine": ["alprazolam", "clonazepam", "diazepam", "lorazepam", "chlordiazepoxide", "etizolam"],
    "opioid": ["tramadol", "tapentadol", "codeine", "morphine", "oxycodone", "fentanyl"],
    "nitrate": ["isosorbide mononitrate", "isosorbide dinitrate", "nitroglycerin", "glyceryl trinitrate", "nicorandil"],
    "pde5": ["sildenafil", "tadalafil", "vardenafil"],
    "ppi_cyp2c19": ["omeprazole", "esomeprazole"],
    "antacid_cation": ["aluminium hydroxide", "magnesium hydroxide", "calcium carbonate", "sucralfate", "ferrous sulphate",
                       "ferrous ascorbate", "zinc sulphate"],
    "triptan": ["sumatriptan", "rizatriptan", "zolmitriptan"],
    "maoi": ["selegiline", "rasagiline", "linezolid"],
    "loop_diuretic": ["furosemide", "torsemide"],
    "aminoglycoside": ["gentamicin", "amikacin", "tobramycin"],
    "corticosteroid": ["prednisolone", "methylprednisolone", "dexamethasone", "deflazacort", "hydrocortisone"],
    "antihistamine_sedating": ["chlorpheniramine", "promethazine", "hydroxyzine", "diphenhydramine", "cyproheptadine"],
}

# (a, b, severity, message). Severity: "major" blocks issuing until acknowledged; "moderate" warns.
INTERACTIONS = [
    ("class:anticoagulant", "class:nsaid", "major", "Bleeding risk: anticoagulant with an NSAID."),
    ("class:anticoagulant", "class:antiplatelet", "major", "Bleeding risk: anticoagulant with an antiplatelet."),
    ("warfarin", "metronidazole", "major", "Metronidazole raises warfarin effect (INR); bleeding risk."),
    ("warfarin", "fluconazole", "major", "Fluconazole raises warfarin effect (INR); bleeding risk."),
    ("warfarin", "class:macrolide", "moderate", "Macrolides can raise INR with warfarin; monitor."),
    ("warfarin", "class:fluoroquinolone", "moderate", "Fluoroquinolones can raise INR with warfarin; monitor."),
    ("class:ssri", "tramadol", "major", "Serotonin syndrome and seizure risk: SSRI with tramadol."),
    ("class:snri", "tramadol", "major", "Serotonin syndrome risk: SNRI with tramadol."),
    ("class:ssri", "class:maoi", "major", "Serotonin syndrome: SSRI with an MAO inhibitor."),
    ("class:ssri", "class:triptan", "moderate", "Serotonin syndrome risk: SSRI with a triptan; monitor."),
    ("class:ssri", "class:nsaid", "moderate", "GI bleeding risk: SSRI with an NSAID; consider gastro-protection."),
    ("class:strong_cyp3a4_inhibitor", "class:statin_cyp3a4", "major",
     "Myopathy / rhabdomyolysis risk: strong CYP3A4 inhibitor with simvastatin/atorvastatin/lovastatin."),
    ("class:ace_inhibitor", "class:k_sparing", "major", "Hyperkalaemia risk: ACE inhibitor with a potassium-sparing diuretic."),
    ("class:arb", "class:k_sparing", "major", "Hyperkalaemia risk: ARB with a potassium-sparing diuretic."),
    ("class:ace_inhibitor", "class:potassium", "moderate", "Hyperkalaemia risk: ACE inhibitor with potassium supplements."),
    ("class:arb", "class:potassium", "moderate", "Hyperkalaemia risk: ARB with potassium supplements."),
    ("class:ace_inhibitor", "class:arb", "major", "Dual RAS blockade (ACE inhibitor + ARB): kidney injury and hyperkalaemia risk."),
    ("class:ace_inhibitor", "class:nsaid", "moderate", "NSAIDs reduce BP control and can harm kidneys with ACE inhibitors."),
    ("class:arb", "class:nsaid", "moderate", "NSAIDs reduce BP control and can harm kidneys with ARBs."),
    ("class:pde5", "class:nitrate", "major", "Severe hypotension: PDE5 inhibitor with a nitrate. Contraindicated."),
    ("class:benzodiazepine", "class:opioid", "major", "Respiratory depression: benzodiazepine with an opioid."),
    ("methotrexate", "trimethoprim", "major", "Bone-marrow toxicity: methotrexate with trimethoprim."),
    ("methotrexate", "cotrimoxazole", "major", "Bone-marrow toxicity: methotrexate with co-trimoxazole."),
    ("methotrexate", "class:nsaid", "moderate", "NSAIDs raise methotrexate levels; monitor."),
    ("clopidogrel", "class:ppi_cyp2c19", "moderate", "Omeprazole/esomeprazole reduce clopidogrel effect; prefer pantoprazole."),
    ("class:fluoroquinolone", "class:antacid_cation", "moderate", "Antacids, iron or zinc block fluoroquinolone absorption; space doses 2+ hours."),
    ("class:tetracycline", "class:antacid_cation", "moderate", "Antacids, iron or zinc block tetracycline absorption; space doses 2+ hours."),
    ("levothyroxine", "class:antacid_cation", "moderate", "Calcium, iron or antacids reduce levothyroxine absorption; space doses 4 hours."),
    ("class:fluoroquinolone", "class:corticosteroid", "moderate", "Tendon rupture risk: fluoroquinolone with a corticosteroid."),
    ("digoxin", "amiodarone", "major", "Amiodarone raises digoxin levels; reduce digoxin dose and monitor."),
    ("digoxin", "class:macrolide", "moderate", "Macrolides can raise digoxin levels; monitor."),
    ("lithium", "class:nsaid", "major", "NSAIDs raise lithium levels: toxicity risk."),
    ("lithium", "class:ace_inhibitor", "major", "ACE inhibitors raise lithium levels: toxicity risk."),
    ("lithium", "class:arb", "major", "ARBs raise lithium levels: toxicity risk."),
    ("theophylline", "ciprofloxacin", "major", "Ciprofloxacin raises theophylline levels: seizure and arrhythmia risk."),
    ("allopurinol", "azathioprine", "major", "Allopurinol greatly raises azathioprine toxicity."),
    ("sildenafil", "class:strong_cyp3a4_inhibitor", "moderate", "CYP3A4 inhibitors raise sildenafil levels; use a lower dose."),
    ("tramadol", "ondansetron", "moderate", "Ondansetron may reduce tramadol effect and adds serotonergic risk."),
    ("class:sulfonylurea", "class:fluoroquinolone", "moderate", "Hypoglycaemia risk: sulfonylurea with a fluoroquinolone."),
    ("class:sulfonylurea", "fluconazole", "moderate", "Fluconazole raises sulfonylurea levels: hypoglycaemia risk."),
    ("class:loop_diuretic", "class:aminoglycoside", "moderate", "Ototoxicity and kidney risk: loop diuretic with an aminoglycoside."),
    ("spironolactone", "class:potassium", "major", "Hyperkalaemia: spironolactone with potassium supplements."),
    ("colchicine", "clarithromycin", "major", "Colchicine toxicity with clarithromycin."),
    ("class:benzodiazepine", "class:antihistamine_sedating", "moderate", "Additive sedation: benzodiazepine with a sedating antihistamine."),
    ("class:opioid", "class:antihistamine_sedating", "moderate", "Additive sedation: opioid with a sedating antihistamine."),
    ("clozapine", "carbamazepine", "major", "Agranulocytosis risk: clozapine with carbamazepine."),
    ("valproate", "carbapenem", "major", "Carbapenems sharply lower valproate levels: seizure risk."),
    ("isotretinoin", "class:tetracycline", "major", "Raised intracranial pressure: isotretinoin with a tetracycline."),
]
# QT: any two QT-prolonging medicines together
QT_MESSAGE = "QT prolongation: two QT-prolonging medicines together; check ECG risk factors."

# Medicines to avoid by age: (salt or class, min_age or None, max_age or None, severity, message)
AGE_CAUTIONS = [
    ("aspirin", None, 16, "major", "Aspirin under 16: Reye's syndrome risk (except specialist use)."),
    ("class:fluoroquinolone", None, 18, "moderate", "Fluoroquinolones under 18: cartilage / tendon risk; use only if no alternative."),
    ("class:tetracycline", None, 8, "major", "Tetracyclines under 8: tooth staining and bone effects."),
    ("codeine", None, 12, "major", "Codeine under 12: risk of fatal respiratory depression."),
    ("tramadol", None, 12, "major", "Tramadol under 12: risk of respiratory depression."),
    ("nimesulide", None, 12, "major", "Nimesulide is banned in India for children under 12."),
    ("promethazine", None, 2, "major", "Promethazine under 2: fatal respiratory depression risk."),
    ("loperamide", None, 2, "major", "Loperamide under 2: ileus and cardiac risk."),
    ("domperidone", None, 12, "moderate", "Domperidone in children: use the lowest dose; QT risk."),
    # Older adults (Beers-style)
    ("class:benzodiazepine", 65, None, "moderate", "Benzodiazepines at 65+: falls, confusion and fracture risk."),
    ("glyburide", 65, None, "moderate", "Glibenclamide at 65+: prolonged hypoglycaemia; prefer gliclazide."),
    ("class:antihistamine_sedating", 65, None, "moderate", "Sedating antihistamines at 65+: anticholinergic confusion, falls."),
    ("amitriptyline", 65, None, "moderate", "Amitriptyline at 65+: anticholinergic and fall risk."),
    ("class:nsaid", 65, None, "moderate", "NSAIDs at 65+: GI bleeding and kidney risk; shortest course, consider gastro-protection."),
]

# Avoid in pregnancy / breastfeeding: (salt or class, applies_to, severity, message)
PREGNANCY_CAUTIONS = [
    ("class:ace_inhibitor", "pregnant", "major", "ACE inhibitors in pregnancy: fetal kidney toxicity. Contraindicated."),
    ("class:arb", "pregnant", "major", "ARBs in pregnancy: fetal toxicity. Contraindicated."),
    ("class:statin", "pregnant", "major", "Statins in pregnancy: avoid."),
    ("warfarin", "pregnant", "major", "Warfarin in pregnancy: embryopathy risk."),
    ("isotretinoin", "pregnant", "major", "Isotretinoin in pregnancy: severe birth defects. Contraindicated."),
    ("valproate", "pregnant", "major", "Valproate in pregnancy: neural-tube defects and developmental harm."),
    ("methotrexate", "pregnant", "major", "Methotrexate in pregnancy: contraindicated."),
    ("misoprostol", "pregnant", "major", "Misoprostol in pregnancy: causes miscarriage."),
    ("class:tetracycline", "pregnant", "major", "Tetracyclines in pregnancy: fetal tooth and bone effects."),
    ("class:fluoroquinolone", "pregnant", "moderate", "Fluoroquinolones in pregnancy: avoid unless no alternative."),
    ("class:nsaid", "pregnant", "moderate", "NSAIDs in pregnancy: avoid, especially after 20 weeks."),
    ("carbimazole", "pregnant", "moderate", "Carbimazole in the first trimester: prefer propylthiouracil."),
    ("class:tetracycline", "breastfeeding", "moderate", "Tetracyclines while breastfeeding: avoid prolonged use."),
    ("amiodarone", "breastfeeding", "major", "Amiodarone while breastfeeding: avoid."),
    ("codeine", "breastfeeding", "major", "Codeine while breastfeeding: infant respiratory depression risk."),
]

# Maximum usual adult daily dose in mg (single-salt products)
MAX_DAILY_MG = {
    "paracetamol": 4000, "ibuprofen": 3200, "diclofenac": 150, "aceclofenac": 200, "naproxen": 1500,
    "mefenamic acid": 1500, "etoricoxib": 120, "aspirin": 4000, "tramadol": 400, "cetirizine": 10, "levocetirizine": 5,
    "fexofenadine": 180, "montelukast": 10, "pantoprazole": 80, "omeprazole": 40, "rabeprazole": 40, "esomeprazole": 40,
    "ondansetron": 24, "domperidone": 30, "metformin": 2550, "glimepiride": 8, "gliclazide": 320, "atorvastatin": 80,
    "rosuvastatin": 40, "amlodipine": 10, "telmisartan": 80, "losartan": 100, "azithromycin": 500, "amoxicillin": 3000,
    "cefixime": 400, "ciprofloxacin": 1500, "levofloxacin": 750, "doxycycline": 200, "metronidazole": 2400,
    "prednisolone": 60, "alprazolam": 4, "clonazepam": 20, "sertraline": 200, "escitalopram": 20, "levothyroxine": 0.3,
}
# Paediatric (under 12) single-salt limits per kg are out of scope: under-12 doses aren't checked against these.
