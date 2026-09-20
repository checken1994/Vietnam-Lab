import re
pattern = re.compile(r'(?i)\b(rm\s+(?:-[a-zA-Z]*[rf][a-zA-Z]*\s+)+\s*/|mkfs|dd\s+if=|chmod\s+-R\s+777|chown\s+-R|> /dev/sda)(?:\s|$)')
cmds = [
    'rm -rf /',
    'rm -r -f /',
    'rm -fr /',
    'rm -f /'
]
for cmd in cmds:
    print(f'{cmd}:', pattern.search(cmd) is not None)
