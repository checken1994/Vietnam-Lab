"""Entry: ``python -m scp.verifier_service --db ... --audit-log ... [--transport stdio|pipe]``."""
from scp.verifier_service.daemon import main

raise SystemExit(main())
