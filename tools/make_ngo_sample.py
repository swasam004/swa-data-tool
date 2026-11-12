"""Creates ngo_beneficiary_register.csv: a KoboToolbox-style export with deliberate problems.
Deterministic (fixed seed). All names/phones are invented."""
import csv
import random
from pathlib import Path

random.seed(7)
first_f = ["Amina", "Grace", "Faith", "Mercy", "Joy", "Lucy", "Esther", "Ruth", "Naomi", "Linet"]
first_m = ["Brian", "Kevin", "Peter", "Samuel", "Allan", "Victor", "Dennis", "Felix", "Hassan", "Moses"]
last = ["Otieno", "Achieng", "Odhiambo", "Wanjiru", "Kamau", "Mutua", "Akinyi", "Onyango", "Njeri", "Ouma"]
wards = ["Ward A", "Ward B", "Ward C", "Ward D"]
programmes = ["Mentorship", "Financial Literacy", "Vocational Skills"]
income_opts = ["farming", "small_business", "casual_labour", "remittances", "none"]

people = []
for i in range(1, 121):
    sex = random.choice(["female", "male"])
    name = f"{random.choice(first_f if sex == 'female' else first_m)} {random.choice(last)}"
    people.append({"code": f"B-{i:04d}", "name": name, "phone": f"07{random.randint(10000000, 99999999)}",
                   "sex": sex, "age": random.randint(11, 34), "ward": random.choice(wards)})

rows, rid = [], 10000
for p in people:
    for prog in random.sample(programmes, random.randint(1, 3)):
        rid += 1
        month = random.randint(1, 9)
        day = random.randint(1, 28)
        opts = random.sample(income_opts[:4], random.randint(0, 2)) or ["none"]
        rows.append([str(rid), p["code"], p["name"], p["phone"], p["sex"], str(p["age"]), p["ward"], prog,
                     f"2025-{month:02d}-{day:02d}T{random.randint(8, 17):02d}:{random.randint(0, 59):02d}:00",
                     str(random.randint(0, 12)), random.choice(["yes", "no", "yes"]), " ".join(opts),
                     str(random.choice([0, 3000, 5000, 8000, 12000, 15000, 25000, 40000]))])
random.shuffle(rows)

# ---- deliberate problems -------------------------------------------------
rows[2][4] = "F"                               # code for female -> mapped
rows[4][4] = "Unknown"                         # not allowed -> quarantined
rows[6][5] = "200"                             # impossible age -> quarantined
rows[8][11] = "farming fishing"                # unknown option -> quarantined
rows[10][8] = "2025-13-45T10:00:00"            # impossible date -> quarantined
rows[12][9] = "twelve"                         # not a number -> quarantined
rows[14][0] = rows[3][0]                       # duplicate record id -> quarantined
rows[16][6] = "ward a"                         # case fixed
rows[18][10] = ""                              # missing training answer (optional) -> shown as (Missing)
rows[20][12] = "KES 8,000"                     # currency text -> parsed
rows.insert(40, [""] * 13)                     # blank row

header = ["_id", "group_reg/beneficiary_code", "group_reg/full_name", "group_reg/phone", "group_demo/sex",
          "group_demo/age", "ward", "programme", "_submission_time", "sessions_attended",
          "completed_training", "income_sources", "monthly_income_kes"]
out = Path(__file__).resolve().parent.parent / "swa_data_tool" / "templates" / "ngo_beneficiary_register.csv"
with open(out, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(header)
    w.writerows(rows)
print("wrote", out, len(rows), "rows")
