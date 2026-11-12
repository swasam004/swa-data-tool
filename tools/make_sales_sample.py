"""Creates sales_sample.csv: realistic data with deliberate problems, so you can
see how the tool reacts. Deterministic (fixed seed)."""
import csv
import random
from pathlib import Path

random.seed(42)
regions = ["North", "South", "East", "West"]
products = ["Laptop", "Printer", "Router", "Cable"]
price = {"Laptop": 55000, "Printer": 18000, "Router": 6500, "Cable": 450}
rows = []
for i in range(1, 201):
    d = random.randint(1, 28); m = random.randint(1, 12)
    p = random.choice(products); u = random.randint(1, 6)
    rows.append([f"ORD-{i:04d}", f"{d:02d}/{m:02d}/2025", random.choice(regions), p, str(u), f"{u * price[p]:,}.00"])

# ---- deliberate problems ------------------------------------------------
rows[3][2] = "nairobi"                       # not in allowed list -> quarantined
rows[5][2] = "  north "                     # case/space -> fixed to "North"
rows[7][5] = "KES " + rows[7][5]             # currency prefix -> parsed
rows[9][5] = "abc"                           # not a number -> quarantined
rows[11][1] = "31/02/2025"                   # impossible date -> quarantined
rows[13][4] = "-2"                           # below minimum -> quarantined
rows[15][5] = "(1,500.00)"                   # negative via brackets -> below min -> quarantined
rows[17][0] = rows[2][0]                     # duplicate Order ID -> quarantined
rows[19][3] = ""                             # missing required Product -> quarantined
rows[21][4] = "2.5"                          # not whole number -> quarantined
rows[23][5] = "12,34.00"                     # bad thousands grouping -> quarantined
rows[25][2] = "EAST"                         # case fixed to "East"
rows.insert(30, ["", "", "", "", "", ""])    # blank row
rows.insert(60, ["ORD-9999", "05/05/2025", "West"])   # malformed (too few fields)

out = Path(__file__).resolve().parent.parent / "swa_data_tool" / "templates" / "sales_sample.csv"
with open(out, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["Order ID", "Date", "Region", "Product", "Units", "Revenue"])
    w.writerows(rows)
print("wrote", out)
