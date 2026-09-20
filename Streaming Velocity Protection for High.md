# Streaming Velocity Protection for High-Cardinality Payment Dimensions

## 1. Problem

We are designing a real-time payment velocity/fraud-protection system operating at approximately:

* **1,000 transactions/sec average**
* **60,000 transactions/minute**
* **3.6 million transactions/hour**
* **86.4 million transactions/day**
* **31.5 billion transactions/year**

Each transaction has approximately 15 dimensions.

The goal is to detect anomalous velocity patterns such as:

```text
BIN
BIN × MCC
BIN × Acquirer
BIN × MCC × Acquirer
BIN × MCC × Acquirer × Channel
...
```

without attempting to maintain exact counters for every possible Cartesian-product combination.

The core challenge is:

> Observe every transaction and detect both high-volume and sudden-change anomalies across arbitrary combinations of dimensions, while keeping memory and per-transaction computation bounded.

---

# 2. The 15 dimensions

For discussion, assume these dimensions:

| Dimension      | Approximate cardinality |
| -------------- | ----------------------: |
| BIN            |                 100,000 |
| MCC            |                   1,000 |
| Acquirer       |                     300 |
| Network        |                      10 |
| Entry Mode     |                      10 |
| Channel        |                      10 |
| ZIP            |               1,000,000 |
| Auth Type      |                       5 |
| Decline Code   |                     200 |
| POS Capability |                      10 |
| Amount Range   |                      20 |
| Region         |                   5,000 |
| Purchase Type  |                      20 |
| Card Product   |                      20 |
| Risk Tier      |                      10 |

These are engineering assumptions, not universal values. Actual cardinalities depend on geography, payment network, product, and data definitions.

Some fields, such as `Decline Code`, are response/outcome fields and therefore are not necessarily available before the initial authorization decision. They can still be used for historical/feedback analysis.

---

# 3. Important distinction: dimension types vs actual combinations

There are:

```text
C(15,1) = 15
C(15,2) = 105
C(15,3) = 455
C(15,4) = 1,365
...
2^15 - 1 = 32,767
```

possible **dimension subsets**.

But this is NOT the actual number of keys.

For example:

```text
BIN × MCC
```

has:

```text
100,000 × 1,000
= 100,000,000
```

actual possible value combinations.

Likewise:

```text
BIN × MCC × Acquirer
=
100,000 × 1,000 × 300
=
30,000,000,000
```

possible value combinations.

Therefore the real problem is the Cartesian product of **values**, not merely the number of dimension subsets.

---

# 4. Theoretical cardinality examples

Assuming one 8-byte counter per possible combination:

| Combination          | Possible keys | Exact counter memory |
| -------------------- | ------------: | -------------------: |
| BIN                  |          100K |               0.8 MB |
| ZIP                  |            1M |                 8 MB |
| BIN × MCC            |          100M |               800 MB |
| BIN × Acquirer       |           30M |               240 MB |
| MCC × Acquirer       |          300K |               2.4 MB |
| BIN × ZIP            |          100B |               800 GB |
| BIN × MCC × Acquirer |           30B |               240 GB |
| BIN × MCC × Network  |            1B |                 8 GB |
| BIN × MCC × ZIP      |          100T |               800 TB |

These numbers are before:

* timestamps
* window management
* hash-table overhead
* metadata
* replication
* persistence
* indexes
* serialization
* fault tolerance

Therefore exact materialization of high-cardinality combinations is impossible.

---

# 5. But theoretical cardinality is not the same as active cardinality

At 1,000 TPS:

```text
1 minute = 60,000 transactions
```

Therefore, during a one-minute window, there can be at most:

```text
60,000 distinct observed combinations
```

of any arbitrary combination type.

For example, although:

```text
BIN × MCC × Acquirer
```

has 30 billion theoretical possibilities, only 60,000 different triple keys can physically appear in a one-minute stream containing 60,000 transactions.

This is one of the most important observations.

We should therefore design around:

> **Observed streaming cardinality + bounded state**

rather than:

> **Theoretical Cartesian-product cardinality.**

---

# 6. The overall architecture

The design uses several layers:

```text
                         Transaction
                              │
             ┌────────────────┼────────────────┐
             │                │                │
             ▼                ▼                ▼
       Exact Singles      Pair Structures    Sampling
             │                │                │
             │                ▼                ▼
             │         Pair anomaly       Higher-order
             │         candidates         discovery
             │                │                │
             └────────────────┴────────────────┘
                              │
                              ▼
                     Candidate / Promotion
                              │
                              ▼
                    Conditional child state
                              │
                              ▼
                    Higher-order candidate
                              │
                              ▼
                         Promotion
                              │
                              ▼
                           Repeat
```

The major mechanisms are:

1. **Exact counters** for low-cardinality individual dimensions.
2. **Exact or CMS-based counters** for all 105 pair types depending on cardinality.
3. **Heavy-hitter structures** to determine which actual values are important.
4. **Short-term and long-term/decayed state** for detecting changes.
5. **Promotion** of interesting combinations.
6. **Conditional child tracking** for promoted combinations.
7. **Sampling** to discover high-order anomalies whose lower-order combinations appear normal.
8. **Exact state + baseline** for promoted combinations.

---

# 7. Level 1 — Individual dimensions

The 15 individual dimensions are cheap enough to track exactly.

For example:

```text
BIN → counter
MCC → counter
Acquirer → counter
Network → counter
...
```

Using 8-byte counters:

```text
BIN:
100,000 × 8 = 0.8 MB

ZIP:
1,000,000 × 8 = 8 MB

Region:
5,000 × 8 = 40 KB
```

The total raw counter storage for all 15 dimensions is only approximately:

```text
~8.85 MB
```

So individual dimensions are not the memory problem.

Implementation:

```pseudo
for dimension in dimensions:
    singles[dimension][transaction[dimension]].increment()
```

In practice, compact arrays or integer IDs are preferable to heavyweight hash maps when the domain is known.

---

# 8. Level 2 — Two-dimensional combinations

There are:

```text
C(15,2) = 105
```

pair types.

Examples:

```text
BIN × MCC
BIN × Acquirer
BIN × Network
BIN × Channel
MCC × Acquirer
MCC × Channel
ZIP × BIN
ZIP × MCC
...
```

The cardinality of each pair type is different.

Therefore we should NOT use the same representation for every pair.

## Low-cardinality pair

Example:

```text
MCC × Acquirer
=
1,000 × 300
=
300,000
```

Exact state:

```text
300,000 × 8 bytes
≈ 2.4 MB
```

This is trivial.

Use exact counters.

## Medium-cardinality pair

Example:

```text
BIN × Acquirer
=
100K × 300
=
30M
```

Exact:

```text
30M × 8
≈ 240 MB
```

Potentially manageable depending on the number of windows, replicas, and nodes.

Could use:

* compact exact counters
* sparse maps
* or CMS if exactness is not required.

## Very high-cardinality pair

Example:

```text
BIN × ZIP
=
100K × 1M
=
100B
```

Exact:

```text
100B × 8
=
800 GB
```

This should use approximate state such as CMS.

---

# 9. Count-Min Sketch for large pair spaces

CMS is acceptable for pair combinations.

For example:

```text
BIN × ZIP
```

can be represented by a fixed-size CMS instead of 100 billion exact counters.

Each transaction:

```pseudo
key = hash(BIN, ZIP)

for row in CMS:
    index = hash_row(key)
    CMS[row][index]++
```

To estimate:

```pseudo
estimate(key):
    return minimum(
        CMS[0][hash0(key)],
        CMS[1][hash1(key)],
        ...
    )
```

CMS gives an overestimate rather than an exact count.

The key design question is therefore:

> How much error can the fraud decision tolerate?

---

# 10. CMS width and depth

CMS parameters should be selected based on the **decision boundary**, not an arbitrary percentage accuracy.

For a standard CMS:

```text
width ≈ e / ε
depth ≈ ln(1 / δ)
```

where:

* `ε` = acceptable additive error as a fraction of total events
* `δ` = probability of exceeding that error

For example, for a one-minute window:

```text
N = 60,000 events
```

If:

```text
ε = 0.001
δ = 0.001
```

then approximately:

```text
width ≈ 2,718
depth ≈ 7
```

This is approximately:

```text
2,718 × 7
≈ 19,000 counters
```

At 4 bytes/counter:

```text
~76 KB
```

This is extremely small compared with an 800 GB exact representation.

However, the correct parameters depend on the smallest threshold that matters.

For example:

```text
Fraud threshold = 1,000/min
CMS error = ±10
```

may be perfectly acceptable.

But:

```text
Fraud threshold = 20/min
CMS error = ±60
```

would be useless.

Therefore:

> **Tune CMS error relative to the minimum operational decision threshold.**

---

# 11. CMS does not solve heavy-hitter discovery by itself

CMS answers:

> "Approximately how many times have I seen this key?"

It does not inherently answer:

> "Which keys are the top K keys?"

Therefore CMS can be combined with a separate heavy-hitter mechanism.

For example:

```text
CMS
+
Space-Saving / bounded heavy-hitter structure
```

CMS:

```text
estimate(key)
```

Heavy-hitter structure:

```text
which keys should I care about?
```

They are complementary.

---

# 12. Detecting anomalies requires more than frequency

High frequency does not automatically mean fraud.

Example:

```text
BIN × MCC = 1,000,000 transactions/hour
```

could be completely normal.

If historically it has done:

```text
950K
1.02M
980K
1.01M
...
```

then 1M is normal.

The useful signal is often:

```text
current behavior
versus
expected behavior
```

Therefore the system needs both:

1. velocity
2. baseline/change detection

---

# 13. Historical baselines

We should NOT maintain a full historical time series for every theoretical combination.

For example, we should not maintain:

```text
100M BIN×MCC keys
×
365 days
×
multiple time buckets
```

That becomes enormous.

Instead use tiers.

## Unpromoted combination

Maintain:

```text
short-term CMS
+
longer-term/decayed approximate state
```

For example:

```text
1-minute CMS
5-minute CMS
1-hour/decayed CMS
```

This provides approximate recent behavior and approximate longer-term behavior.

## Promoted combination

Once a combination becomes important:

```text
exact counters
+
EWMA baseline
+
short-term windows
```

For example:

```text
baseline(t) =
    α × current_rate
    +
    (1-α) × baseline(t-1)
```

Now the important combination gets a dedicated historical baseline.

---

# 14. How an unpromoted pair can become interesting without a historical baseline

We cannot claim:

> "This BIN×MCC is historically anomalous"

if we have never maintained historical state for it.

Instead, it can become a candidate based on:

### Absolute velocity

```text
BIN×MCC = 20,000/min
```

### Short-vs-long-term approximate behavior

```text
short_rate / long_rate
```

### Concentration

For example:

```text
BIN total = 30,000/min

BIN×MCC = 25,000/min
```

so:

```text
83% of the BIN's traffic
```

is concentrated in one MCC.

### Other signals

* decline rate
* amount distribution
* geographic concentration
* network concentration
* sudden appearance
* transaction-rate acceleration

Once it becomes sufficiently interesting, promote it.

---

# 15. Promotion

Promotion means:

> "This combination has earned dedicated state."

For example:

```text
BIN=411111 × MCC=5812
```

becomes a promoted candidate.

Create a node:

```text
CandidateNode(
    dimensions = [BIN, MCC],
    values = [411111, 5812]
)
```

The node now gets:

* exact short-term counters
* baseline/EWMA
* child distributions
* child heavy-hitter structures
* anomaly scoring

---

# 16. Critical distinction: top-K is NOT anomaly detection

Suppose the promoted candidate is:

```text
BIN=411111 × MCC=5812
```

and we track Acquirer children:

```text
A17 = 18,500
A22 = 1,000
A02 = 200
...
```

A17 is the top child.

That does NOT mean:

> A17 is anomalous.

It only means:

> A17 contributes the most traffic to the parent candidate.

Therefore:

### Heavy hitters answer:

> Which child values explain the parent's traffic?

### Baselines answer:

> Is a specific child behaving abnormally?

These are different jobs.

---

# 17. Conditional child tracking

When a parent candidate is promoted:

```text
BIN × MCC
```

start tracking its children.

For each remaining dimension:

```text
Acquirer
Channel
Network
Region
EntryMode
ZIP
...
```

maintain bounded child state.

For example:

```text
BIN×MCC
    │
    ├── Acquirer → child heavy hitters
    ├── Channel → child heavy hitters
    ├── Network → child heavy hitters
    ├── Region → child heavy hitters
    └── ...
```

For every transaction matching the parent:

```pseudo
if transaction.matches(parent):

    for dimension in expandableDimensions:

        value = transaction[dimension]

        parent.childHH[dimension].offer(value)
```

---

# 18. Why would A17 be selected?

We never arbitrarily choose A17.

Suppose:

```text
BIN×MCC total = 20,000 / 5 min

Acquirer:
A17 = 18,500
A22 = 1,000
A02 = 200
others = 300
```

The data tells us that A17 explains most of the parent traffic.

Therefore A17 becomes a candidate for further investigation.

The system did NOT say:

```text
"Let's check A17 because we chose it."
```

It said:

```text
"Let's find which Acquirers explain the parent anomaly."
```

The same applies to Channel.

If:

```text
BIN×MCC×A17
```

has:

```text
ECOM = 18,000
POS  = 300
MOTO = 20
```

then ECOM is selected because it explains the traffic.

---

# 19. Top-K implementation

A regular map could become unbounded.

Instead use a bounded heavy-hitter structure such as Space-Saving.

Example:

```text
K = 20
```

For a candidate:

```text
Acquirer → top 20
Channel → top 20
Network → top 20
Region → top 20
...
```

Pseudo-implementation:

```pseudo
function observeChild(dimension, value):

    if value exists in table:
        table[value].count++
        return

    if table.size < K:
        table[value] = {
            count: 1,
            error: 0
        }
        return

    victim = minimumCountEntry(table)

    oldCount = victim.count

    replace victim with:
        value = value
        count = oldCount + 1
        error = oldCount
```

This gives bounded memory.

---

# 20. What time period does top-K represent?

It should be **windowed**.

For velocity protection, useful windows might be:

```text
1 minute
5 minutes
1 hour
24 hours
```

For example:

```text
Candidate:
BIN×MCC

1-minute child HH
5-minute child HH
1-hour child/baseline
```

The exact set depends on the attack patterns we care about.

Old state expires.

For example, a one-minute structure can use rotating buckets:

```text
bucket[0] = current minute
bucket[1] = previous minute
...
bucket[N] = older
```

Then:

```text
5-minute rate =
sum(last 5 buckets)
```

---

# 21. The baseline problem for A17

This is the subtle issue.

Suppose the parent was promoted at 10:00.

At 10:01:

```text
A17 = 18,000
```

We have no historical A17 baseline.

Therefore we cannot honestly say:

```text
A17 is 50× its historical baseline.
```

Instead:

```text
A17 is a high-volume child of the newly promoted parent.
```

We start collecting its baseline from the moment the parent is promoted.

For example:

```text
10:00
Parent promoted

10:00–10:30
Warm-up period

10:30+
A17 baseline available
```

After sufficient observations:

```text
A17 current = 4,000/min
A17 baseline = 500/min

ratio = 8×
```

Now we have an actual child anomaly signal.

---

# 22. How do we avoid missing attacks during warm-up?

Use multiple signals.

During warm-up, the child can still be considered suspicious based on:

* absolute velocity
* parent concentration
* sudden acceleration
* decline-rate changes
* amount anomalies
* network/channel concentration
* known rules
* sampled observations

So there are two concepts:

```text
Current-volume candidate
```

versus:

```text
Historical anomaly
```

Do not confuse them.

---

# 23. High-cardinality child dimensions

Not every child dimension should use the same structure.

Example:

### Acquirer

Only ~300 values.

Use:

```text
exact counters + EWMA
```

### Channel

~10 values.

Use:

```text
exact counters + EWMA
```

### Network

~10 values.

Use:

```text
exact counters + EWMA
```

### Region

~5,000 values.

Still potentially manageable per promoted candidate, but if many candidates are promoted, use:

```text
bounded heavy hitters
+
approximate state
```

### ZIP

Potentially ~1M values.

Definitely use:

```text
heavy hitters / CMS / sampling
```

Thus the child representation depends on cardinality.

---

# 24. Higher-order combinations

Suppose:

```text
BIN × MCC
```

is promoted.

Conditional analysis finds:

```text
Acquirer=A17
```

Then:

```text
BIN × MCC × A17
```

becomes a promoted node.

Its child dimensions are now:

```text
Channel
Network
Region
EntryMode
ZIP
...
```

Suppose:

```text
Channel=ECOM
```

becomes significant.

Then:

```text
BIN × MCC × A17 × ECOM
```

becomes a candidate.

This repeats:

```text
BIN
 ↓
BIN×MCC
 ↓
BIN×MCC×Acquirer
 ↓
BIN×MCC×Acquirer×Channel
 ↓
BIN×MCC×Acquirer×Channel×Region
 ↓
...
```

But only suspicious branches are expanded.

---

# 25. The problem with only hierarchical expansion

There is an important failure mode.

Suppose:

```text
BIN×MCC
```

looks completely normal.

But:

```text
BIN×MCC×Acquirer×Channel
```

is anomalous.

If the architecture only expands anomalous parents, it will never discover this combination.

Therefore we need an independent discovery path.

---

# 26. Sampling path

Sample a small percentage of transactions.

For example:

```text
1% sampling
```

At 1,000 TPS:

```text
10 sampled transactions/sec
```

These transactions can receive substantially more expensive analysis.

For sampled transactions, evaluate selected higher-order combinations.

For example:

```pseudo
if random() < SAMPLE_RATE:

    for selectedTripleType:
        tripleCMS[selectedTripleType].increment(
            hash(transaction values)
        )

    for selectedQuadType:
        quadCMS[selectedQuadType].increment(
            hash(transaction values)
        )
```

The sampling path exists to catch:

> high-order anomalies whose lower-order projections do not themselves look anomalous.

---

# 27. Do not enumerate all higher-order combinations per sampled transaction

There are:

```text
455 triples
1,365 quadruples
3,003 five-way combinations
...
```

Even at 1% sampling, blindly generating every possible combination is unnecessary.

Instead use:

* selected dimension combinations
* configurable policies
* attack-specific combinations
* combinations discovered from lower levels
* random subset of higher-order dimension types

The sampling budget should be explicitly bounded.

---

# 28. Complete write path

A transaction has three write responsibilities.

```pseudo
function processTransaction(tx):

    # ----------------------------------
    # 1. INDIVIDUAL DIMENSIONS
    # ----------------------------------

    for dimension in availableDimensions(tx):

        singles[dimension]
            .increment(tx[dimension])


    # ----------------------------------
    # 2. ALL 105 PAIR TYPES
    # ----------------------------------

    for pairType in configuredPairTypes:

        key = makePairKey(tx, pairType)

        if pairType.useExact:
            pairExact[pairType].increment(key)
        else:
            pairCMS[pairType].increment(key)


    # ----------------------------------
    # 3. EXISTING PROMOTED CANDIDATES
    # ----------------------------------

    candidates =
        candidateIndex.lookup(tx)

    for candidate in candidates:

        candidate.shortWindow.increment()

        for dimension
            in candidate.expandableDimensions:

            value = tx[dimension]

            candidate.childTracker[dimension]
                .observe(value)


    # ----------------------------------
    # 4. SAMPLING
    # ----------------------------------

    if random() < SAMPLE_RATE:

        higherOrderDiscovery.observe(tx)
```

---

# 29. Candidate lookup

You cannot scan every promoted candidate.

Suppose there are 10,000 promoted candidates.

For each transaction, scanning all 10,000 would be unacceptable.

Therefore maintain an inverted index.

Example:

```text
BIN=411111
    →
    candidate IDs 12, 81, 903

MCC=5812
    →
    candidate IDs 81, 903, 5001
```

For a transaction:

```pseudo
candidateIDs =
    intersection(
        index[BIN=411111],
        index[MCC=5812],
        ...
    )
```

Only matching candidates are evaluated.

This is effectively an inverted-index lookup.

---

# 30. Detection/read path

The synchronous fraud decision should remain bounded.

For each incoming transaction:

```pseudo
function detect(tx):

    alerts = []

    # Look up relevant promoted candidates
    candidates = candidateIndex.lookup(tx)

    for candidate in candidates:

        currentRate =
            candidate.shortWindow.rate(tx)

        baseline =
            candidate.baseline.estimate()

        score =
            anomalyScore(
                currentRate,
                baseline,
                concentration,
                declineRate,
                otherSignals
            )

        if score >= ALERT_THRESHOLD:
            alerts.add(candidate)

    return alerts
```

The read path should NOT perform expensive historical scans.

Everything required for the decision should already exist in bounded in-memory state.

---

# 31. Promotion path

Promotion should generally be separated from the synchronous transaction path.

```pseudo
function promotionLoop():

    candidates =
        collectCandidateSignals()

    for candidate in candidates:

        if candidate.score < PROMOTION_THRESHOLD:
            continue

        if candidate.alreadyPromoted:
            continue

        promote(candidate)
```

Promotion creates:

```text
exact short-term state
+
baseline/EWMA
+
child trackers
+
candidate-index entries
```

---

# 32. Promotion lifecycle

A candidate progresses through states:

```text
UNSEEN
   ↓
OBSERVED
   ↓
CANDIDATE
   ↓
PROMOTED / WARMING
   ↓
BASELINE_READY
   ↓
ANOMALY_DETECTED
   ↓
HOT
```

It can also be demoted:

```text
HOT
 ↓
inactive
 ↓
TTL expiry
 ↓
removed
```

This prevents state from growing forever.

---

# 33. Promotion should have TTLs

A combination that was suspicious six months ago may be irrelevant now.

Therefore promoted nodes need expiration.

Example:

```text
candidate TTL = 1 hour
hot TTL = 24 hours
baseline retention = configurable
```

If activity continues, refresh the TTL.

This keeps the system bounded.

---

# 34. Memory model

Suppose we cap:

```text
MAX_PROMOTED_NODES = 10,000
K = 20 child values
```

A promoted node might have approximately:

```text
14 remaining dimensions
×
20 child entries
=
280 child entries
```

Worst case:

```text
10,000 × 280
=
2.8 million child entries
```

This is bounded.

The system therefore does NOT care whether the theoretical space contains:

```text
30B
100T
10^15
```

possible combinations.

Only the number of **currently promoted nodes and their bounded children** determines this part of memory.

---

# 35. Important separation of responsibilities

The system should maintain four conceptually different things.

## 1. Frequency state

Question:

> How many times did I observe this key?

Implemented with:

* exact counter
* CMS

## 2. Heavy hitters

Question:

> Which actual values are contributing most to this parent?

Implemented with:

* Space-Saving
* bounded top-K structure

## 3. Baseline

Question:

> What is normal behavior for this already-important key?

Implemented with:

* EWMA
* rolling windows
* potentially seasonality-aware baselines later

## 4. Promotion tree

Question:

> Which higher-order combinations deserve dedicated state?

Implemented as:

```text
parent candidate
    ↓
conditional child distributions
    ↓
candidate children
    ↓
promotion
```

These are not interchangeable.

---

# 36. The key example

Consider:

```text
BIN=411111
MCC=5812
Acquirer=A17
Channel=ECOM
Region=IN-SOUTH
```

The system might discover it as follows:

### Stage 1

```text
BIN×MCC
```

shows:

```text
20,000/min
```

and becomes a candidate.

### Stage 2

Conditional Acquirer distribution:

```text
A17 = 18,500
A22 = 1,000
...
```

A17 explains most of the traffic.

Create:

```text
BIN×MCC×A17
```

### Stage 3

Conditional Channel distribution:

```text
ECOM = 18,000
POS = 300
...
```

ECOM explains most of the traffic.

Create:

```text
BIN×MCC×A17×ECOM
```

### Stage 4

Conditional Region distribution:

```text
IN-SOUTH = 17,000
...
```

Create:

```text
BIN×MCC×A17×ECOM×IN-SOUTH
```

### Stage 5

That combination now has dedicated state:

```text
short-term velocity
longer-term baseline
EWMA
decline rate
amount distribution
etc.
```

The important point is:

> A17 and ECOM were never hardcoded. They emerged from conditional distributions of the parent candidate.

---

# 37. But "A17 is top" is not sufficient

Suppose:

```text
A17:
current = 18,500/min
historical baseline = 18,000/min
```

Then A17 is dominant but not anomalous.

Another Acquirer:

```text
A22:
current = 1,000/min
historical baseline = 10/min
```

may be much more anomalous.

Therefore candidate expansion should consider both:

```text
contribution
+
anomaly/change
```

not merely frequency.

A useful conceptual score is:

```text
childScore =
    anomalyMagnitude
    × contribution
```

or some more carefully calibrated combination.

Do not simply choose the top-K by raw count.

---

# 38. What happens when a child has no historical baseline?

Use three states:

### Known baseline

```text
baseline exists
→ calculate deviation
```

### Newly discovered child

```text
no baseline
→ current-volume/concentration signals only
→ begin collecting baseline
```

### Mature child

```text
enough observations
→ historical anomaly detection enabled
```

This avoids pretending to know historical behavior that was never stored.

---

# 39. Why not maintain historical baselines for every possible pair?

Because the theoretical state becomes enormous.

Instead:

```text
All possible keys
    ↓
approximate short/long-term observation
    ↓
candidate
    ↓
promotion
    ↓
exact baseline
```

This is the central memory-saving mechanism.

---

# 40. Final conceptual model

The system should be thought of as an **adaptive anomaly-detection tree**, not a giant multidimensional counter.

```text
                        All traffic
                            │
                            ▼
                    Individual dimensions
                            │
                            ▼
                     105 pair types
                            │
             ┌──────────────┴──────────────┐
             ▼                             ▼
       Exact pairs                    CMS pairs
             │                             │
             └──────────────┬──────────────┘
                            ▼
                    Candidate detection
                            │
                            ▼
                     Promote parent
                            │
                ┌───────────┼───────────┐
                ▼           ▼           ▼
             Acquirer    Channel     Network
                │
          child distribution
                │
                ▼
           candidate child
                │
                ▼
       parent × child promoted
                │
                ▼
       next-level dimensions
                │
                ▼
               ...
```

The system's fundamental strategy is:

> **Observe everything cheaply, maintain exact state where cardinality is small, use sketches where cardinality is large, identify important child values with bounded heavy-hitter structures, create historical baselines only for promoted combinations, and recursively expand only branches that justify their memory cost.**

There is one additional safety mechanism:

> **Sampling provides an escape hatch for high-order anomalies whose lower-order combinations never become anomalous enough to trigger hierarchical expansion.**

This gives us three complementary discovery mechanisms:

```text
Hierarchical expansion
        +
Approximate global observation
        +
Random sampling
```

rather than relying on any single technique.

---

# 41. The next implementation questions

The architecture above leaves several parameters that need to be engineered quantitatively:

1. Exact vs CMS threshold for each of the 105 pair types.
2. CMS width/depth for each cardinality class.
3. Number of short-term buckets.
4. Long-term baseline mechanism and decay rate.
5. Heavy-hitter K.
6. Maximum promoted nodes.
7. Maximum children per promoted node.
8. Sampling percentage.
9. Number of higher-order combinations examined per sampled transaction.
10. Promotion threshold.
11. Demotion/TTL policy.
12. Synchronous read-path latency budget.
13. Total memory per 1,000 TPS.
14. Horizontal partitioning strategy.
15. How to merge CMS/heavy-hitter state across partitions.
16. How to handle hot keys and skew.
17. How to prevent CMS collision/error from causing false promotions.
18. How to calculate the actual anomaly score.

The most important next step is to put **actual numbers against these parameters** and calculate the total memory and CPU cost for the 1,000 TPS workload.
