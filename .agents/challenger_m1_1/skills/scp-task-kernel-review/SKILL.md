# SCP Task Kernel Review Skill
(Dumped copy for Challenger M1)

Capabilities to verify:
- Task identity: ID, owner, deadline, version, risk profile
- State machine: Valid transitions, preconditions/postconditions, atomic OCC
- Event journal: Append-only, sequence increment, hash chain
- Lease: TTL, heartbeat, fencing, stale lease rejection
- Verifier: Independent, evidence-based, cryptographic receipt verification
- Recovery: Proper classification (retryable, fail-closed, terminal)
- Fail-Closed: Never reach COMPLETED without valid verification evidence
