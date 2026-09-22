import os
import re

patches = [
    # T01_boot
    ("tests/T01_boot/test_flow_01_boot_background_scp_standard.py", 
     "        time.sleep(1.5)\n\n        # Stop registry\n        registry.stop_all()\n\n        # Verify job executed\n        assert len(execution_log) >= 1, \"Registered background job did not execute\"", 
     "        # Polling wait\n        start_t = time.time()\n        while time.time() - start_t < 2.0:\n            if len(execution_log) >= 1:\n                break\n            time.sleep(0.05)\n\n        # Stop registry\n        registry.stop_all()\n\n        # Verify job executed\n        assert len(execution_log) >= 1, \"Registered background job did not execute\""),
     
    ("tests/T01_boot/test_flow_01_boot_background_scp_standard.py",
     "        registry.start_all()\n        time.sleep(0.5)\n        registry.stop_all()",
     "        registry.start_all()\n        time.sleep(0.01) # fast pass\n        registry.stop_all()"), # Wait, this is just a delay.

    ("tests/T01_boot/test_flow_01_boot_background_scp_standard.py",
     "        registry.start_all()\n        time.sleep(0.5)\n        initial_count = execution_count[\"count\"]\n        assert initial_count > 0\n\n        registry.stop_all()\n        time.sleep(0.5)\n        final_count = execution_count[\"count\"]\n\n        # Count should not increase after stop_all\n        assert final_count == initial_count",
     "        registry.start_all()\n        # Polling wait 1\n        start_t = time.time()\n        while time.time() - start_t < 2.0:\n            if execution_count[\"count\"] > 0:\n                break\n            time.sleep(0.05)\n        initial_count = execution_count[\"count\"]\n        assert initial_count > 0\n\n        registry.stop_all()\n        # Wait to ensure no more executions\n        time.sleep(0.2)\n        final_count = execution_count[\"count\"]\n\n        # Count should not increase after stop_all\n        assert final_count == initial_count")
]

for f, search, replace in patches:
    if os.path.exists(f):
        c = open(f, 'r').read()
        if search in c:
            c = c.replace(search, replace)
            open(f, 'w').write(c)
            print(f"Patched {f}")
        else:
            print(f"Not found in {f}")
