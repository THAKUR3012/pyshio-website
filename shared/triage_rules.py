"""Symptom triage rules: emergency red flags (always checked first) and symptom -> speciality hints.

English, Hinglish (Roman) and some Hindi (Devanagari). Red flags are deliberately broad: a false alarm costs a
phone call; a missed emergency costs much more. This is not a diagnosis; it points people to the right kind of doctor.
"""
import re

EMERGENCY_CALL = "108 (ambulance) or 112"

# (id, pattern, message)
RED_FLAGS = [
    ("chest_pain", r"chest (pain|tight|pressure|heav)|pain in (my |the )?chest|seen[ae] (me|mein|main) (dard|bhari|jalan|dabav)|"
                   r"chhati (me|mein) dard|सीने में दर्द|छाती में दर्द",
     "Chest pain or pressure can be a heart attack."),
    ("breathing", r"(can'?t|cannot|unable to|difficulty|trouble|hard to) breath|breathless|short(ness)? of breath|gasping|"
                  r"saa?ns (nahi|nahin|lene me|lene mein|phool|ruk|band)|दम घुट|सांस (नहीं|लेने में)",
     "Serious difficulty breathing needs urgent care."),
    ("stroke", r"face (droop|is drooping|tedha)|munh tedha|slurred speech|(can'?t|cannot) (speak|talk) (properly|suddenly)|"
               r"(one|1)[ -]?side[d]? (weak|numb|paralys)|sudden (weakness|numbness|confusion)|lakwa|paralysis|"
               r"bolne (me|mein) (dikkat|takleef|taklif)|लकवा",
     "Sudden weakness, a drooping face or slurred speech can be a stroke: every minute matters."),
    ("unconscious", r"unconscious|passed out|faint(ed|ing)|not waking|unresponsive|behosh|बेहोश|hosh (nahi|nahin)",
     "Fainting or not being fully awake needs urgent care."),
    ("seizure", r"seizure|convuls|\bfits\b|fit (aa|aaya|pad)|mirgi|daura (pad|aaya)|मिर्गी|दौरा",
     "A seizure (fit) needs urgent care, especially a first one or one lasting over 5 minutes."),
    ("bleeding", r"(heavy|severe|lot of|lots of|bahut|zyada) (bleeding|blood|khoon)|vomit(ing|ed)? blood|blood (in|with) (vomit|ulti)|"
                 r"khoon (ki|wali) ulti|cough(ing)? (up )?blood|khoon (ki|wali) khansi|bleeding (in|during) pregnancy|"
                 r"pregnan\w*.{0,30}(bleed|khoon)|black (stool|potty)",
     "Heavy bleeding, or blood in vomit or cough, needs urgent care."),
    ("suicide", r"suicid|kill (myself|himself|herself)|end (my|his|her) life|want to die|don'?t want to live|"
                r"marna chaht[ae]|jeena nahi|khudkushi|aatmahatya|आत्महत्या",
     "You don't have to face this alone. Please talk to someone now: Tele-MANAS 14416 (free, 24×7), or 112 in an emergency."),
    ("poisoning", r"poison|overdose|zeher|ज़हर|जहर|swallowed (pills|tablets|acid|kerosene|phenyl)|too many (pills|tablets)|"
                  r"pesticide|keetnashak",
     "Poisoning or an overdose needs emergency care now. Take the bottle or strip with you."),
    ("anaphylaxis", r"(face|lips?|tongue|throat) (swell|swollen|sooj)|anaphyla|gala (band|sooj)|hont (sooj|phool)",
     "Swelling of the face, lips or throat can be a severe allergic reaction."),
    ("head_injury", r"head injury|hit (my|his|her|the) head|sir (par|pe|me|mein) chot|road accident|accident (hua|ho gaya)|"
                    r"fell from (height|stairs|roof)",
     "A head injury or accident needs to be checked urgently."),
    ("meningitis", r"stiff neck|neck (is )?stiff|gardan akad",
     "Fever with a stiff neck or confusion needs urgent care."),
    ("severe_abdomen", r"severe (stomach|abdominal|abdomen|belly) pain|unbearable (stomach|abdominal) pain|"
                       r"pet (me|mein) (bahut|tez|asahniya) dard",
     "Severe stomach pain needs to be checked urgently."),
    ("bite", r"snake ?bite|saa?np (ne )?(kaat|dasa|kata)|dog bite|kutte ne (kaata|kata|kaat)|scorpion|bichhu",
     "Snake, scorpion and dog bites need prompt medical care (and vaccines for dog bites)."),
    ("burn", r"severe burn|badly burn|jal gay[ae] (bahut|zyada)|acid attack",
     "Serious burns need emergency care."),
]
INFANT_WORDS = r"newborn|new born|infant|\bbaby\b|navjaat|नवजात|(\d|one|two|three)\s*(week|weeks|month|months|mahine)\s*(old|ka|ki)"
FEVER_WORDS = r"fever|bukhar|बुखार|temperature|garam"

# (pattern, specialities in settings.SPECIALITIES, label)
SYMPTOM_MAP = [
    (r"fever|bukhar|बुखार|cold|zukam|sardi|cough|khansi|खांसी|\bflu\b|viral|body ?ache|badan dard|sore throat|gala kharab|"
     r"infection|typhoid|dengue|malaria",
     ["Fever & infections", "Family medicine"], "fever, cough or infection"),
    (r"asthma|wheez|dama|breathing problem|cough for (weeks|months)|copd|tb\b|tuberculosis",
     ["Pulmonology", "Cardio-respiratory"], "breathing and lungs"),
    (r"sugar|diabetes|diabetic|madhumeh|very thirsty|frequent urination|baar baar peshab",
     ["Diabetes", "Family medicine"], "diabetes / sugar"),
    (r"\bbp\b|blood pressure|hypertension|high bp|low bp",
     ["Hypertension", "Cardiology", "Family medicine"], "blood pressure"),
    (r"palpitation|dhadkan|heart|dil (me|mein|ki)|cholesterol",
     ["Cardiology"], "heart"),
    (r"rash|itch|khujli|acne|pimple|muhase|eczema|skin|twacha|hair ?fall|baal (jhad|gir)|dandruff|daad|fungal|psoriasis|allergy on skin",
     ["Dermatology"], "skin and hair"),
    (r"tooth|teeth|daant|दांत|gum|masood|cavity|toothache|braces|wisdom",
     ["General dentistry", "Root canal", "Orthodontics"], "teeth and gums"),
    (r"back ?pain|kamar (me|mein)? ?dard|knee|ghutn|joint|jodon|shoulder|kandh|neck pain|gardan (me|mein)? ?dard|sprain|moch|"
     r"fracture|haddi|arthritis|gathiya|slip disc|sciatica",
     ["Orthopaedics", "Orthopaedic", "Pain management"], "bones, joints and back"),
    (r"sports|gym injury|ligament|\bacl\b|tennis elbow|running injury",
     ["Sports injury", "Orthopaedics"], "sports injury"),
    (r"stomach|pet (me|mein)? ?dard|pet kharab|acidity|gas\b|vomit|ulti|diarrh|dast|loose motion|constipation|kabz|indigestion|"
     r"liver|jaundice|piliya|piles|bawaseer",
     ["Gastroenterology", "Family medicine"], "stomach and digestion"),
    (r"period|\bmc\b|masik|pregnan|garbh|pcos|pcod|white discharge|safed paani|menopause|fertility|conceive",
     ["Gynaecology", "Women's health"], "women's health"),
    (r"child|baby|bachch|bacche|infant|\bkid|toddler|newborn|my son|my daughter|beta|beti",
     ["Paediatrics", "Paediatric"], "children"),
    (r"\bear\b|kaan|nose|naak|sinus|tonsil|hearing|sunai|snoring|kharrate|nose ?bleed",
     ["ENT"], "ear, nose and throat"),
    (r"headache|sir ?dard|सिर दर्द|migraine|numb|tingling|jhunjhun|dizz|vertigo|chakkar|tremor|memory|bhool",
     ["Neurology", "Family medicine"], "nerves and headaches"),
    (r"anxiety|ghabrahat|stress|tension|depress|udaas|sad all|neend|sleep|insomnia|panic|addiction|nasha|ocd",
     ["Psychiatry"], "mind and sleep"),
    (r"stroke recovery|after (surgery|operation)|post[- ]?(surgery|op)|rehab|physio|paralysis recovery|walking (problem|difficulty)",
     ["Post-surgical rehab", "Neurological", "Orthopaedic"], "rehabilitation"),
    (r"elderly|old age|budhap|frequent falls|my (father|mother|dad|mom|papa|mummy) is (old|\d{2})",
     ["Geriatric", "Family medicine"], "older adults"),
    (r"urine|peshab|पेशाब|burning (urination|while urinating)|kidney stone|pathri|uti\b",
     ["Family medicine"], "urine and kidneys"),
    (r"\beye|aankh|vision|nazar",
     ["Family medicine"], "eyes (a GP can refer you to an eye specialist)"),
]
DEFAULT_SPECIALITIES = ["Family medicine"]


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().replace("’", "'")).strip()


def red_flags(text: str, age=None) -> list:
    t = normalise(text)
    hits = [(fid, msg) for fid, pattern, msg in RED_FLAGS if re.search(pattern, t)]
    infant = (age is not None and age < 1) or re.search(INFANT_WORDS, t)
    if infant and re.search(FEVER_WORDS, t):
        hits.append(("infant_fever", "Fever in a baby under 3 months needs a doctor today, urgently."))
    return hits


def symptom_matches(text: str) -> list:
    """[(score, specialities, label)] best first."""
    t = normalise(text)
    out = []
    for pattern, specs, label in SYMPTOM_MAP:
        found = [m.group(0) for m in re.finditer(pattern, t)]
        if found:
            out.append((sum(len(f) or 1 for f in found), specs, label))
    return sorted(out, key=lambda x: -x[0])
