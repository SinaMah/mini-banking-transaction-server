# Bonus feasibility

Neither bonus is implemented. Finish and record the mandatory two-VM version first.

## Advanced bonus: primary and backup, up to +10%

The PDF requires at least three VMs: two banking servers and a client. The backup must take over automatically after the primary fails while requests are active. Confirmed transactions must not disappear, and an ambiguous retry must not apply twice.

A correct extension needs durable replication before success, unique transaction identifiers, persisted duplicate detection and cached results, ordered application on the backup, leader/failover control, and a client that resolves uncertain outcomes using the same identifier. Promotion must account for partially received or committed requests. Restoring SQLite from a periodic copy is insufficient: a confirmed write can be lost between copies, and a retry can duplicate an applied operation. Ordinary SQLite WAL is local crash recovery, not replication between servers.

Difficulty is high relative to the mandatory project. The banking calculations are small; replication and failure semantics are the substantial work. The existing storage boundary gives a starting point, but adding transaction records and a replication protocol changes the design and tests. A heartbeat alone does not establish whether a server is dead or disconnected.

Three lightweight VMs might fit within 8 GB RAM with careful allocation and a quiet Windows host. That has not been measured here. Disk capacity is the more immediate concern: another two guest installations, snapshots, and growing disks are not a safe assumption with about 10 GB free on the same host drive. Increase free space or use another suitable drive before attempting this bonus.

## Extreme bonus: network partition and split brain, +15%

The PDF requires a real network cut between two server VMs while both stay running, followed by restoration and recovery. A stopped process is not that demonstration. The client must remain able to exercise the reachable side(s) during the partition.

Two peers cannot determine from silence whether the other is dead or isolated. Promoting both on heartbeat timeout risks accepting conflicting writes. A defensible design either uses an independent quorum/witness with exclusive leadership and fencing, allowing writes only on the authorized side, or deliberately stops writes when safe leadership cannot be established. Loss of write availability during a partition can preserve correctness, but the instructor must assess whether the complete proposed design meets the bonus expectations. Do not promise that two isolated peers can both keep writing safely to independent balances.

Recovery needs authoritative committed ordering, catch-up of the returning replica, persisted duplicate handling, and exclusion of stale leaders. Add tests for delayed messages, recovery, client requests that lose replies, and the old primary returning. A majority-based three-voter design is a possible later direction, not a feature of this package.

This is substantially harder than process-crash failover and may require another VM or independently reachable witness. Avoid additional services now. The practical next step is the mandatory deployment and its evidence; reconsider bonus work only after that passes and hardware space is available.
