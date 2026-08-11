from pathlib import Path

p = Path(__file__).parent / "database.py"
lines = p.read_text(encoding="utf-8").splitlines(keepends=True)

out = []
skip = False
depth = 0

for i, line in enumerate(lines):
    if (
        not skip
        and "# FLOW ROLES" in line
        and i > 500
        and i < 600
    ):
        skip = True
        continue

    if skip:
        if line.strip() == "# PLC":
            prev = lines[i - 1].strip() if i > 0 else ""
            prev2 = lines[i - 2].strip() if i > 1 else ""
            if prev.startswith("# -") and prev2.startswith("# -"):
                skip = False
                out.append(lines[i - 2])
                out.append(lines[i - 1])
                out.append(line)
        continue

    out.append(line)

p.write_text("".join(out), encoding="utf-8")
print(f"removed {len(lines) - len(out)} lines")
