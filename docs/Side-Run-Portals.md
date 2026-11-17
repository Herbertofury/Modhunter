# Side-run portals: Siege adapter

This slice implements Siege. Realm/Shiverfell's adapter is **not implemented**: it needs a complete transient enemy/projectile/cooldown snapshot and its own return tests. The shared codec does not imply Realm integration.

Random simulation-time discovery places an Astral Detour nearby after120–240seconds. Approach it and press E or its button, then choose10/20/30minutes. A fresh child run starts only after the complete losslessly compressed parent and child state have been durably acknowledged by native storage or successfully written to local storage. The parent timer, position, health, enemies, projectiles, gems, skills, companions and cooldowns stay frozen. One level of nesting is allowed; entering a second side portal is rejected.

A visible return button extracts at any time. Completion at the selected duration or defeat also restores the parent. Earned simulation XP and unspent rerolls above the child's initial allotment transfer once; child damage does not hurt the parent. No native ranked currency or verified update reward is created. E during a child still mines/recruits rather than unexpectedly extracting. Starting a fresh run/Atlas contract cannot overwrite the saved parent.

## Persistence and races

The native profile stores the packed parent separately from the existing750KB child-state budget, keyed by child and parent identity, in the same atomic/revisioned write. The parent archive has its own750KB packed and8MB decoded codec limits. The authenticated Siege request route supports9MiB; limits reject explicitly without truncating. Normal return atomically writes the parent and removes the archive. Tests send a near-budget compressed parent plus512entities through actual local HTTP above the former800KB transport limit.

Entry/return wait for outstanding regular saves and then their own exact acknowledgement before switching active state. Simulation/input freeze during this operation. Lost acknowledgements reconcile only against an exact observed saved snapshot for the same profile; they do not blindly resend or grant another reward. Invalid archives stay retained behind a retry dialog. Storage failures leave the last good snapshot unchanged. Canceling the duration chooser changes nothing. Closing the game overlay during a pending operation hides the game but is not a promise to cancel an already submitted durable write.

Parent gzip support requires browser CompressionStream/DecompressionStream. Unsupported browsers fail entry without replacing the parent. Malformed/incomplete/oversized decoded data is rejected. Saved child runs reopen with the compressed parent and earned reward record intact. Treasure encounter references rebind immediately on restoration to prevent a first-frame reward-identity mismatch.

## Integration and remaining acceptance

This isolated draft stacks on loot-goblin PR43. Nemesis PR41 separately changes native Siege persistence. **Source-merge both optional archive paths**: retain rivals allowlist/packing/reconstruction while adding portal archive packing/reconstruction; neither backend version can simply overwrite the other. Regenerate both HTML bundles after source/dependency/test integration.

Source/VM tests cover all durations, main20minute floor preservation, double entry/return, parent timer/health/skills, reload, death return, corrupt snapshot, cancellation, native in-flight acknowledgement, quota rejection and uncertain-response reconciliation. Native HTTP tests cover archive roundtrip, large combined payload, revision conflict and last-good retention. Windows WebView2 visuals/controller, actual long-session gameplay/FPS, browser compression availability and Realm adapter remain pending. This is not full two-mode portal completion or release acceptance.
