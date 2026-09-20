import sys

path = 'tests/T04_kernel/test_autonomous_governor.py'
with open(path, 'r', encoding='utf8') as f:
    content = f.read()

old_str = """        "action": "os.write_file",
        "params": {"file_path": str(tmp_path.parent / "outside.txt"), "content": "bad"},"""

new_str = """        "action": "pc.write_file",
        "params": {"file_path": str(tmp_path.parent / "outside.txt"), "content": "bad"},"""

content = content.replace(old_str, new_str)

with open(path, 'w', encoding='utf8') as f:
    f.write(content)
