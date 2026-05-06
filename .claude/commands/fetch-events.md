# fetch-events

Fetch Resident Advisor events for a given city and genre, then save them to the local database.

## Usage

```
/fetch-events --city <city> --genre <genre> [--limit <n>]
```

## What to do

1. Run `python main.py fetch --city $CITY --genre $GENRE --limit $LIMIT` where the arguments come from the user's message
2. Report how many events were saved and list their names
3. If any events already existed in the database, say so rather than saving duplicates

## Examples

- `/fetch-events berlin techno` — fetch techno events in Berlin
- `/fetch-events london house 10` — fetch up to 10 house events in London
