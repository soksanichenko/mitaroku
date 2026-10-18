# Anime source and ID mapping: spike report

Date run: 2026-10-18. Plan: `docs/superpowers/plans/2026-10-10-plan-0-anime-source-spike.md`. The spike code lives in `spikes/anime_mapping/` and is throwaway.

## Question

Which providers supply anime franchise structure, episode lists with air dates, and a bridge to TMDB ids, so that the data model and provider contracts rest on measured facts?

## Method

- 12 fixtures (see `spikes/anime_mapping/fixtures.py`): Frieren, Attack on Titan, One Piece, FMA: Brotherhood, Bakemonogatari, Fate/stay night, Steins;Gate, Demon Slayer, Jujutsu Kaisen, Mushishi, Cowboy Bebop, Your Name.
- Franchise structure from AniList relations (no key). ID bridge from `Fribb/anime-lists` (`anime-list-full.json`, 39,619 entries). Probes with a Simkl client id and a TMDB read token (both available). TVDB was not probed (excluded by the spec). Jikan was unreachable and Kitsu timed out from the development machine.
- Two comparisons: the AniList grouping against the Yamtrack fork's relation rules; a TMDB-only episode allocation (all seasons concatenated and sliced, or Fribb season and offset) against Simkl, scored by whether the first allocated episode aired within 2 days of the AniList start date and the allocation length equals the AniList episode count (the first run used a looser 14-day window; both are reported).

## Results

### Coverage

| Title | Main entries | Extras | In Fribb | With Simkl id | With TMDB id | With IMDb id | AniList upcoming episodes | Simkl relations | Simkl episodes (dated) | TMDB seasons |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Frieren | 3 | 3 | 3/3 | 3/3 | 3/3 | 3/3 | 0 | 2 | 53 (53) | 2 |
| Attack on Titan | 9 | 7 | 8/9 | 8/9 | 7/9 | 7/9 | 0 | 12 | 40 (35) | 5 |
| One Piece | 2 | 47 | 2/2 | 2/2 | 2/2 | 2/2 | 9 | 26 | 1235 (1227) | 24 |
| Fullmetal Alchemist: Brotherhood | 1 | 4 | 1/1 | 1/1 | 1/1 | 1/1 | 0 | 3 | 86 (85) | 2 |
| Bakemonogatari | 13 | 5 | 13/13 | 9/13 | 9/13 | 9/13 | 0 | 13 | 18 (18) | 6 |
| Fate/stay night: UBW | 9 | 10 | 9/9 | 6/9 | 6/9 | 6/9 | 0 | 45 | 14 (14) | 3 |
| Steins;Gate | 3 | 2 | 3/3 | 2/3 | 2/3 | 2/3 | 0 | 7 | 27 (27) | 2 |
| Demon Slayer | 9 | 0 | 9/9 | 7/9 | 7/9 | 7/9 | 0 | 16 | 26 (26) | 6 |
| Jujutsu Kaisen | 6 | 0 | 6/6 | 6/6 | 5/6 | 5/6 | 0 | 6 | 46 (46) | 2 |
| Mushishi | 6 | 0 | 6/6 | 5/6 | 5/6 | 5/6 | 0 | 4 | 39 (36) | 3 |
| Cowboy Bebop | 1 | 3 | 1/1 | 1/1 | 1/1 | 1/1 | 0 | 1 | 40 (28) | 2 |
| Your Name | 1 | 0 | 1/1 | 1/1 | 1/1 | 1/1 | 0 | 2 | 14 (14) | n/a |

Notes on the table: "TMDB seasons" counts season 0 (specials) as a season. "AniList upcoming episodes" was measured on the root entry of each fixture only. The 39 TV/ONA entries become 38 in the scoring because the ONA *MONSTERS* maps to a TMDB movie, not a TV show.

### Hand-checked chains

Hand-check (2026-10-18 run):
- Attack on Titan: chain correct and ordered; 9 entries = 6 TV cours + OVA + 2 specials (Final Season Kanketsu-hen). Fribb gives season+offset (S3 pt2 off 12, Final Season pt2 off 16, Kanketsu off 28).
- Bakemonogatari: 13 entries in release order; Fribb maps Nekomonogatari (Kuro), Hanamonogatari, Tsukimonogatari to TMDB season 0 (specials); 4 of 13 have no TMDB/Simkl id (3 Kizumonogatari movies + Zoku Owarimonogatari OVA).
- Fate/stay night: sequel/prequel chain merges 4 adaptations (2006, Zero, UBW, Heaven's Feel) under 3 different TMDB shows (37858, 45845, 61415); entries with Fribb season=1 for several shows. Heaven's Feel movies have no TMDB id.
- Demon Slayer: TV cours map to TMDB seasons 1-5 cleanly; compilation movies map to TMDB movie ids; two upcoming movies have no start date and no id.

### Comparison with the Yamtrack fork's rules

| Title | ours main | fork main | only ours | only fork | extras ours/fork | skipped edges |
| --- | --- | --- | --- | --- | --- | --- |
| Frieren | 3 | 3 | [] | [] | 3/3 | 0 |
| Attack on Titan | 9 | 11 | [] | [20691, 20692] | 7/4 | 5 |
| One Piece | 2 | 2 | [] | [] | 47/46 | 2 |
| Fullmetal Alchemist: Brotherhood | 1 | 1 | [] | [] | 4/2 | 3 |
| Bakemonogatari | 13 | 13 | [] | [] | 5/4 | 1 |
| Fate/stay night: UBW | 9 | 9 | [] | [] | 10/1 | 37 |
| Steins;Gate | 3 | 2 | [11577] | [] | 2/2 | 1 |
| Demon Slayer | 9 | 9 | [] | [] | 0/0 | 2 |
| Jujutsu Kaisen | 6 | 5 | [204066] | [] | 0/1 | 2 |
| Mushishi | 6 | 6 | [] | [] | 0/0 | 0 |
| Cowboy Bebop | 1 | 1 | [] | [] | 3/3 | 0 |
| Your Name | 1 | 1 | [] | [] | 0/0 | 0 |

Fork comparison verdicts (9/12 identical main chains):
- 20691, 20692 (AoT Zenpen/Kouhen movies, only fork): recap compilations of season 1, linked by ALTERNATIVE. Fork wrongly promotes them to main by walking through extras; ours keeps them as extras. Ours right.
- 11577 (Steins;Gate Fuka Ryouiki no Deja vu movie, only ours): canon continuation film with PREQUEL OVA + PARENT edge to the TV show. Fork demotes it via PARENT; ours keeps it main. Ours arguably right.
- 204066 (JJK Shibuya Jihen compilation movie, only ours): recap of season 2 arc with SEQUEL+PARENT edges. Fork demotes it to extra. Fork right; ours wrongly main.
- Fate: fork skips 37 ALTERNATIVE/SPIN_OFF edges, ours lists 10 extras vs fork 1.
Net: neither rule is strictly better; a PARENT edge on a MOVIE/compilation is the signal the fork uses well, and walking through extras is the fork's flaw. Candidate rule for plan 2: ours + demote entries having a PARENT edge when format is MOVIE or SPECIAL.

### TMDB-only episode scoring

Two measures are reported. The *14-day* columns are the original loose check: the first allocated episode aired within 14 days of the AniList start date. The *guard* columns are the stricter check adopted after the independent review: the first episode within 2 days **and** the allocation length equal to the AniList episode count. Every correct allocation in the data starts on the exact start date; the only accepted non-zero deltas under the loose check were two Fate/stay night: Unlimited Blade Works cours, both one episode off (TMDB has 12 episodes where AniList counts 13 including a prologue), and two Bakemonogatari entries had the wrong length (12 for 15, 23 for 26). The loose check therefore overstated the result.

| Title | entries | concat (14d) | fribb (14d) | hybrid (guard) | simkl (guard) | fribb fits season | unresolved by hybrid |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Frieren | 3 | 2 | 2 | 2 | 2 | 2/2 | Sousou no Frieren 3rd Seas |
| Attack on Titan | 6 | 6 | 6 | 6 | 6 | 6/6 |  |
| One Piece | 1 | 1 | 0 | 1 | 1 | 0/0 |  |
| Fullmetal Alchemist: Brotherhood | 1 | 1 | 1 | 1 | 1 | 1/1 |  |
| Bakemonogatari | 9 | 1 | 4 | 3 | 4 | 6/8 | Nekomonogatari (Kuro); Monogatari Series: Second ; Hanamonogatari; Tsukimonogatari; Owarimonogatari (Ge); Monogatari Series: Off & M |
| Fate/stay night: UBW | 5 | 5 | 3 | 3 | 4 | 2/5 | Fate/stay night: Unlimited; Fate/stay night: Unlimited |
| Steins;Gate | 1 | 1 | 1 | 1 | 1 | 1/1 |  |
| Demon Slayer | 5 | 5 | 5 | 5 | 5 | 5/5 |  |
| Jujutsu Kaisen | 3 | 3 | 2 | 3 | 2 | 2/2 |  |
| Mushishi | 3 | 3 | 3 | 3 | 2 | 3/3 |  |
| Cowboy Bebop | 1 | 1 | 1 | 1 | 0 | 1/1 |  |
| Your Name | 0 | 0 | 0 | 0 | 0 | 0/0 |  |
| TOTAL | 38 | 29 | 28 | 29 | 28 | 29/34 | |

Notes on the misses (hybrid = Fribb season and offset first, then the concatenated stream, first candidate that passes the guard):

- Frieren season 3 is unaired (start 2027-10): no dates anywhere. Not a method failure.
- Six Monogatari entries are unresolved. Three (Nekomonogatari (Kuro), Hanamonogatari, Tsukimonogatari) are mapped by Fribb to TMDB season 0 (specials), but their TMDB specials do not line up by count. Owarimonogatari (Ge) and Off & Monster Season are a Fribb error: Fribb maps Owarimonogatari (Ge) to TMDB season 5, which is the 2024 Off & Monster season, and maps Off & Monster to a season 6 that does not exist. AniList episode counts also exceed TMDB season sizes (season 1: 15 against 12; season 3: 26 against 23), which breaks cumulative slicing.
- Two Fate/stay night: Unlimited Blade Works entries: the length check rejects TMDB's 12-episode season against AniList's 13.
- One Piece fails under Fribb alone (no season for a single absolute-numbered entry) but passes by concatenation.
- Simkl under the same guard misses Cowboy Bebop (first episode undated) and entries whose episode list also contains recap episodes (Mushishi, Jujutsu Kaisen).
- The Fribb season and offset pair fits inside the TMDB season length for 29 of 34 checkable entries (5 more have no checkable pair, among them One Piece).

## Findings

- **Coverage.** Of 63 main-chain entries, 62 (98%) have a Fribb record, 51 (81%) a Simkl id, 49 (78%) a TMDB id and 49 an IMDb id. Every one of the 39 TV and ONA entries has both a TMDB and a Simkl id. The gaps are compilation movies (8 of 17 movies have a TMDB id), OVAs (0 of 3) and some specials (2 of 4). The weakest fixtures are Bakemonogatari and Fate/stay night (9 of 13 and 6 of 9 with ids), because of unlisted movies and OVAs.
- **Season and offset.** Fribb's `season` and `episode_offset` map Attack on Titan exactly (season 3 part 2 at offset 12, the final season part 2 at offset 16, the Kanketsu-hen special at offset 28). Overall, 29 of 34 checkable TV entries fit inside their TMDB season length (5 more have no checkable pair). The misses are Fate (Fribb attaches several adaptations to one TMDB season), Bakemonogatari (AniList episode counts exceed TMDB season sizes) and Jujutsu Kaisen (TMDB has one 59-episode season where Fribb says season 2). So Fribb alone is not enough; the date check is needed.
- **AniList chain.** For the four hand-checked titles the sequel/prequel chain is ordered by start date and contains the expected cours. Against the Yamtrack rules 9 of 12 fixtures have identical main chains; that agreement is weak evidence, because both rule sets read the same AniList data and share the SEQUEL/PREQUEL core. Where they differ, the fork is wrong on two Attack on Titan recap movies (it walks through extras), and our grouping is wrong on the Jujutsu Kaisen Shibuya compilation movie. Known weaknesses remain on both sides: Jujutsu Kaisen's second compilation movie (AniList 192809, linked by ALTERNATIVE with no PARENT edge) is main in both groupings; Fate/stay night chains four alternative adaptations into one main chain (a product choice, not judged here); the Steins;Gate continuation film (11577, a MOVIE with a PARENT edge) is in our main chain and would be demoted by the fork's PARENT rule. An honest count is about 9 to 10 of 12 fixtures fully right.
- **Air dates.** AniList `airingSchedule` has data only for running shows (9 entries for One Piece, 0 elsewhere). TMDB season episodes carry air dates, titles, overviews and stills for aired episodes. Simkl has dates for most episodes but not all (Attack on Titan 35 of 40, Cowboy Bebop 28 of 40). Unaired seasons (Frieren season 3) have no dates anywhere. The main run queried `airingSchedule` only for upcoming episodes on the 12 root entries (so "0 upcoming" in the coverage table means roots only). A follow-up probe (`probe_anilist_extras.py`) on the 8 entries the guard left to the degraded mode (excluding Frieren season 3) found: `averageScore` present for all 8; the full `airingSchedule` complete for 3 of 8 (Off & Monster 14 of 14, both Fate/stay night: Unlimited Blade Works entries 13 of 13) and nearly empty for the older Monogatari entries (0 to 2 nodes); `streamingEpisodes` unreliable (26 titles for a 13-episode show, 1 for a 4-episode one, 0 for Off & Monster). So the degraded mode may rely on AniList for the episode count, the start date and the rating, and for per-episode dates only when `airingSchedule` has as many nodes as episodes.

## Decision rules and outcome

- **Franchise source: AniList, with a known weakness for compilation movies.** The chain is right for the four hand-checked titles; across all fixtures about 9 to 10 of 12 are fully right (see Findings), which is at the 10-of-12 threshold, not clearly above it. The fork's PARENT rule (a MOVIE or SPECIAL with a PARENT edge is an extra) fixes the Jujutsu Kaisen movie 204066 but demotes the Steins;Gate film 11577, so it is a candidate rule, not an adopted one. Test cases for plan 2: 11577, 192809, 204066.
- **ID bridge: Fribb.** Coverage is 98% of main-chain entries, above the 90% threshold. Refresh the dataset on a schedule. Do not trust its season and offset blindly: Fribb has at least one outright season error (Owarimonogatari (Ge)), and 5 of 34 checkable pairs do not fit TMDB.
- **Episode source: TMDB behind a guard, with a degraded mode.** The hybrid (Fribb season and offset, then the concatenated stream) with the guard (first episode within 2 days and allocation length equal to the AniList count) resolves 29 of 38 TV/ONA entries (76%). The other 9 (24%: six Monogatari entries, two Fate/stay night: Unlimited Blade Works entries, and the unaired Frieren season 3) go to a degraded mode: episode count and start date from AniList, no per-episode titles or dates. Per-episode past dates from AniList were not measured, so the degraded mode promises only those.
- **Simkl necessity: not needed for the MVP, but the deciding reason is the licence, not coverage.** Under the same guard Simkl resolves 28 of 38, no better than TMDB, because its lists mix in recap episodes and some are undated. Without Simkl, the fields per episode come from:

  | Field per episode | Source without Simkl | Gap |
  | --- | --- | --- |
  | Title, overview, still | TMDB (aired episodes) | 24% of TV/ONA entries fall to the degraded mode and have none |
  | Air date (aired) | TMDB | the same 24% |
  | Air date (upcoming) | AniList `airingSchedule`, TMDB | none measured |
  | Count | AniList `episodes` | none |

  Simkl would cover several of those entries only under its login-and-sync obligation (Simkl API rule 2), which is outside the MVP; adding it later needs its own decision.

## Consequences for the spec

Edits applied to `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md`:

- Section 4: the provider table drops MAL and Simkl and adds AniList (anime entries, relations, upcoming schedule, anime rating) and Fribb (ID bridge). TMDB also supplies anime episodes. The paragraph on id mapping now describes the bridge, the guard and the degraded mode.
- Section 13: the finished spike item is removed. New open items: the Fribb refresh schedule and the degraded-mode UX, whether to add the Simkl provider later with login and sync, and the PARENT rule on movies and specials, with the test cases 11577, 192809 and 204066.

## Requirements carried to plan 2

Findings from the independent review that belong in the real provider code, not in the throwaway spike:

- Index the Fribb dataset by `anilist_id` as well as by MAL id; AniList is the franchise source, and at least one entry (AniList 162314) has no MAL id.
- AniList client: parse `Retry-After` as either seconds or an HTTP date; retry on 5xx and timeouts, not only on 429.
- Do not drop AniList relation edges whose `format` is null (freshly announced entries); keep them as unresolved extras.
- Sort entries with an unknown start date last, consistently in every ordering.
- Treat the PARENT rule as a candidate with the test cases AniList 11577, 192809 and 204066.
- Apply the guard (first episode within 2 days and allocation length equal to the AniList count) before showing TMDB episode titles, and fetch TMDB season 0 when Fribb maps an entry to it.
