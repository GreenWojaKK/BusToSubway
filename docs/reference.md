# Reference

This reference defines the public-facing concepts, schemas, parameters, and algorithms. It avoids design justification; see [Explanation](explanation.md) for that.

The names below describe the intended public API. The internal engineering pipeline may use more specific stage names while the package is being stabilized.

## Glossary

| term | definition |
|---|---|
| **Quay** | Individual boarding/alighting point, usually one physical stop. |
| **StopPlace** | Logical station-like grouping of one or more Quays. |
| **L-space** | Graph representation where nodes are stops or StopPlaces and edges connect consecutive stops on a route. |
| **Dominance** `s(v)` | Hub suitability score used for local comparison. Default: L-space degree. |
| **Ego subgraph** `N_k[v]` | Nodes within shortest-path distance `k` from `v`, including `v`, with induced edges. |
| **Lifetime** | Largest radius `k` where `v` remains dominant inside `N_k[v]`. |
| **Corridor stop** | Stop on a shared through-corridor; many routes pass, but they mostly move together. |
| **Interchange-capable stop** | Stop or StopPlace where routes split, cross, or reconnect in a way that can support transfers. |

## Input Schemas

### `stops.csv`

One row per Quay.

| column | type | required | description |
|---|---|---:|---|
| `stop_id` | string | yes | Unique stop identifier. Prefix with city/operator if needed. |
| `name` | string | yes | Human-readable stop name. |
| `lat` | float | yes | Latitude in WGS84. |
| `lon` | float | yes | Longitude in WGS84. |
| `direction` | string | no | Optional direction or platform hint. |
| `city_code` | string | no | Optional city/operator namespace. |

### `route_stops.csv`

One row per stop visit in a route pattern or direction.

| column | type | required | description |
|---|---|---:|---|
| `route_id` | string | yes | Route-pattern or route-direction identifier. |
| `seq` | integer | yes | Stop order within `route_id`. |
| `stop_id` | string | yes | Foreign key to `stops.csv`. |

`route_id` should not merge opposite directions. If it does, the L-space graph will include false edges around turnarounds.

## Output Schemas

### `stopplace_map.csv`

| column | description |
|---|---|
| `stop_id` | Quay identifier. |
| `stopplace_id` | Assigned StopPlace identifier. |
| `stopplace_name` | Representative name. |
| `method` | Grouping method or override source. |
| `review_flag` | Optional marker for manual inspection. |

### `lspace_edges.csv`

| column | description |
|---|---|
| `source`, `target` | StopPlace or stop node ids. |
| `n_routes` | Number of routes contributing this adjacency. |
| `routes` | Delimited list of contributing routes, if retained. |
| `distance_m` | Optional geographic distance between node centroids. |

### `hubs.csv`

| column | description |
|---|---|
| `node_id` | StopPlace or stop node id. |
| `degree` | Number of L-space neighbors. |
| `dominance` | Score used for local comparison. |
| `lifetime` | Survival radius. `0` means the node is not dominant even at `k=1`. |
| `rank_degree` | Rank by degree, descending. |
| `rank_lifetime` | Rank by lifetime, descending. |

## Algorithms

### L-space

Let `G = (V, E)` be an undirected graph.

- `V` is the set of stops or StopPlaces.
- `(u, v) in E` if at least one `route_id` visits `u` and `v` consecutively.
- Multiple routes may contribute to the same edge, but the base graph is simple.

If StopPlace grouping is used, route sequences are first mapped from `stop_id` to `stopplace_id`. Consecutive duplicate StopPlaces are collapsed to avoid self-loops.

### Dominance

The default dominance score is:

```text
s(v) = degree_G(v)
```

Other scores may be used if documented, such as weighted degree, service frequency, or centrality. Changing `s` changes the meaning of lifetime.

### Lifetime

Given graph `G`, score `s`, tie rule `▷`, and maximum radius `k_max`:

```text
lifetime(v) = max { k in [1, k_max] :
                    s(v) ▷ s(u) for every u in N_k[v] \ {v} }
```

If no radius satisfies the condition:

```text
lifetime(v) = 0
```

Tie rules:

| rule | condition |
|---|---|
| `strict` | `s(v) > s(u)` |
| `ge` | `s(v) >= s(u)` |

Because `N_k[v]` only grows as `k` increases, dominance can only be maintained or lost; it cannot be lost and then recovered under a fixed global score.

### StopPlace Grouping

The public grouping interface should be conservative:

1. generate spatial candidates within `eps` meters;
2. compare normalized names;
3. reject direction conflicts when direction metadata is available;
4. create connected components as StopPlaces;
5. emit diagnostics for close-but-unmerged and far-but-same-name cases.

Manual overrides should be explicit and auditable. Automatic cross-name merging should be avoided unless the rule is narrow and reviewable.

## Parameters

| parameter | default | unit | description |
|---|---:|---|---|
| `k_max` | 6 | hops | Maximum ego radius for lifetime. |
| `tie` | `strict` | enum | `strict` or `ge`. |
| `metric` | `degree` | enum/callable | Dominance score. |
| `eps` | 40 | meters | StopPlace spatial candidate radius. |
| `name_sim` | 0.85 | ratio | Name similarity threshold. |
| `seed` | 0 | integer | Reproducibility for any randomized tie handling or sampling. |

## CLI Shape

```bash
python -m bustosubway group \
  --stops stops.csv \
  --eps 40 \
  --name-sim 0.85 \
  --output stopplace_map.csv

python -m bustosubway run \
  --stops stops.csv \
  --routes route_stops.csv \
  --stopplaces stopplace_map.csv \
  --k-max 6 \
  --output outputs/

python -m bustosubway export \
  --hubs outputs/hubs.csv \
  --format geojson \
  --output outputs/hubs.geojson
```

## Explorer Controls

| control | behavior |
|---|---|
| metric toggle | switch map color/rank between degree, dominance, and lifetime |
| k slider | inspect dominance at a selected ego radius |
| StopPlace toggle | compare Quay-level and StopPlace-level graphs, if both are available |
| threshold filter | show top N or minimum lifetime |
| diagnostics layer | display grouping and hub-review candidates |
