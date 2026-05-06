# draft-email

Draft a personalized outreach email for a saved event.

## Usage

```
/draft-email --event-id <id> [--tone <formal|casual>]
```

## What to do

1. Run `python main.py draft --event-id $ID --tone $TONE`
2. Show the drafted email to the user
3. Ask if they want to refine it (adjust tone, length, angle) before saving

## Notes

- Default tone is casual — DJs usually know each other
- Drafts are saved to the database but never sent automatically
