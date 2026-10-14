from pathlib import Path
p=Path('verification/a3_1/verify_candidate.py');s=p.read_text().replace("protected=json.loads((root/'verification/a3_1/protected-before.json').read_text())", "protected={name.replace('\\\\','/'):sha for name,sha in json.loads((root/'verification/a3_1/protected-before.json').read_text()).items()}");p.write_text(s)
