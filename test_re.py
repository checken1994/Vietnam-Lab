import re
cmd = r'rm -r\f /'
normalized_command = re.sub(r'[\'\"\\]', '', cmd)
pattern = re.compile(r'(?i)\b(rm\s+-(?:[A-Za-z]*[rf][A-Za-z]*)\s+/|mkfs|dd\s+if=|chmod\s+-R\s+777|chown\s+-R|> /dev/sda)(?:\s|$)')
print('cmd:', repr(cmd))
print('normalized:', repr(normalized_command))
print('match on normalized:', pattern.search(normalized_command) is not None)
