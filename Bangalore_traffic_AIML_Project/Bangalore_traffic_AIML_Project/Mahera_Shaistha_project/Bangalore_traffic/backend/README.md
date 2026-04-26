# Bangalore Traffic Backend

## Setup

```bash
cd backend
pip install -r requirements.txt
uvicorn app:app --reload
```

## Environment

Create `backend/.env`:

```env
TOMTOM_API_KEY=your_tomtom_key
OPENWEATHER_API_KEY=your_openweather_key
GEOAPIFY_API_KEY=your_geoapify_key
OPENAI_API_KEY=your_openai_key
OPENAI_MODEL=gpt-5-mini
```

## Endpoints

- `GET /health`
- `GET /autocomplete?text=...`
- `GET /weather?lat=12.9716&lon=77.5946`
- `GET /traffic-flow?lat=12.9716&lon=77.5946`
- `GET /incidents`
- `GET /nearby-places?lat=12.9716&lon=77.5946&type=hospital`
- `GET /traffic-tile?lat=12.9716&lon=77.5946&zoom=12`
- `GET /traffic-tiles-template`
- `POST /route-plan`
- `GET /areas`
- `POST /predict`
- `POST /ai-insights`
- `POST /ai-chat`

### route-plan payload

```json
{
  "source_text": "Majestic, Bangalore",
  "destination_text": "Whitefield, Bangalore",
  "depart_in_minutes": 120
}
```
