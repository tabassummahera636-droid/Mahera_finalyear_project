import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import L from 'leaflet'
import './App.css'

const API_BASE = '/api'

function useDebouncedValue(value, delay = 80) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(id)
  }, [value, delay])
  return debounced
}

function severityFromDelay(delaySeconds) {
  const d = Number(delaySeconds || 0)
  if (d >= 1200) return { label: 'High', cls: 'sev-high' }
  if (d >= 600) return { label: 'Medium', cls: 'sev-medium' }
  return { label: 'Low', cls: 'sev-low' }
}

function routeTrafficLevel(delayMinutes) {
  const d = Number(delayMinutes || 0)
  if (d >= 15) return { label: 'Heavy', cls: 'level-heavy' }
  if (d >= 7) return { label: 'Moderate', cls: 'level-moderate' }
  return { label: 'Light', cls: 'level-light' }
}

function offsetPolyline(polyline = [], dLat = 0.0006, dLon = -0.0006) {
  return polyline.map((pt) => {
    if (!Array.isArray(pt) || pt.length < 2) return pt
    return [pt[0] + dLat, pt[1] + dLon]
  })
}

function ensureRealRoutes(routes = []) {
  return (routes || []).filter((r) => Array.isArray(r?.polyline) && r.polyline.length >= 2)
}

function normalizePolyline(input) {
  if (!input) return []
  if (Array.isArray(input)) {
    if (input.length && Array.isArray(input[0])) {
      return input
        .map((pt) => [Number(pt[0]), Number(pt[1])])
        .filter((pt) => Number.isFinite(pt[0]) && Number.isFinite(pt[1]))
    }
    if (input.length && typeof input[0] === 'object') {
      return input
        .map((pt) => {
          const lat = pt.lat ?? pt.latitude
          const lon = pt.lon ?? pt.lng ?? pt.longitude
          return [Number(lat), Number(lon)]
        })
        .filter((pt) => Number.isFinite(pt[0]) && Number.isFinite(pt[1]))
    }
  }
  return []
}

function haversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371
  const toRad = (v) => (v * Math.PI) / 180
  const dLat = toRad(lat2 - lat1)
  const dLon = toRad(lon2 - lon1)
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)))
}

function bearingDeg(a, b) {
  const toRad = (v) => (v * Math.PI) / 180
  const toDeg = (v) => (v * 180) / Math.PI
  const lat1 = toRad(a[0])
  const lat2 = toRad(b[0])
  const dLon = toRad(b[1] - a[1])
  const y = Math.sin(dLon) * Math.cos(lat2)
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon)
  return (toDeg(Math.atan2(y, x)) + 360) % 360
}

function turnDirection(prevBearing, nextBearing) {
  const diff = ((nextBearing - prevBearing + 540) % 360) - 180
  if (Math.abs(diff) < 30) return null
  return diff > 0 ? 'Turn right' : 'Turn left'
}

function generateStepsFromPolyline(polyline = []) {
  const line = normalizePolyline(polyline)
  if (line.length < 2) return []
  const steps = []
  let segmentDistance = 0
  let lastBearing = null
  for (let i = 1; i < line.length; i += 1) {
    const prev = line[i - 1]
    const curr = line[i]
    const distKm = haversineKm(prev[0], prev[1], curr[0], curr[1])
    segmentDistance += distKm
    const b = bearingDeg(prev, curr)
    if (lastBearing != null) {
      const turn = turnDirection(lastBearing, b)
      if (turn) {
        steps.push({ text: `${turn} at the next junction`, lat: curr[0], lon: curr[1] })
      }
    }
    lastBearing = b
    if (segmentDistance >= 0.5) {
      steps.push({ text: `Continue for ${(segmentDistance * 1000).toFixed(0)} m`, lat: curr[0], lon: curr[1] })
      segmentDistance = 0
    }
  }
  if (!steps.length) {
    steps.push({ text: 'Continue on the main road', lat: line[line.length - 1][0], lon: line[line.length - 1][1] })
  }
  return steps.slice(0, 12)
}

function pointToPolylineDistanceKm(lat, lon, polyline = []) {
  if (!Array.isArray(polyline) || !polyline.length) return Infinity
  let minDistance = Infinity
  for (const point of polyline) {
    if (!Array.isArray(point) || point.length < 2) continue
    const dist = haversineKm(lat, lon, Number(point[0]), Number(point[1]))
    if (dist < minDistance) minDistance = dist
  }
  return minDistance
}

function routeDistanceKm(route) {
  const line = normalizePolyline(route?.polyline)
  if (line.length >= 2) {
    let total = 0
    for (let i = 1; i < line.length; i += 1) {
      total += haversineKm(line[i - 1][0], line[i - 1][1], line[i][0], line[i][1])
    }
    if (Number.isFinite(total) && total > 0) return total
  }
  const fallback = Number(route?.distance_km || 0)
  return Number.isFinite(fallback) ? fallback : 0
}

function getTrafficColor(speed) {
  if (speed > 40) return '#00FF00'
  if (speed > 20) return '#FFA500'
  if (speed > 10) return '#FF0000'
  return '#8B0000'
}

function getTrafficLabel(speed) {
  if (speed > 40) return 'Smooth'
  if (speed > 20) return 'Moderate'
  if (speed > 10) return 'Heavy'
  return 'Severe'
}

function trafficBucket(speed) {
  if (speed > 40) return 'smooth'
  if (speed > 20) return 'moderate'
  if (speed > 10) return 'heavy'
  return 'severe'
}

function generateMockTrafficSegments(center, count = 120) {
  const { lat, lon } = center || { lat: 12.9716, lon: 77.5946 }
  const segments = []
  for (let i = 0; i < count; i += 1) {
    const baseLat = lat + (Math.random() - 0.5) * 0.18
    const baseLon = lon + (Math.random() - 0.5) * 0.2
    const len = 0.01 + Math.random() * 0.03
    const angle = Math.random() * Math.PI * 2
    const lat2 = baseLat + Math.cos(angle) * len
    const lon2 = baseLon + Math.sin(angle) * len
    const speed = Math.round(5 + Math.random() * 55)
    segments.push({
      id: `seg-${i}`,
      coordinates: [
        [baseLat, baseLon],
        [lat2, lon2],
      ],
      speed,
    })
  }
  return segments
}

function RouteBadge({ label = 'Recommended' }) {
  return <span className="route-badge">{label}</span>
}

function LevelBadge({ level }) {
  return (
    <span className={`traffic-pill ${level.cls}`}>
      <span className={`level-icon ${level.cls}`} aria-hidden="true" />
      {level.label}
    </span>
  )
}

function clamp01(v) {
  return Math.max(0, Math.min(1, Number(v) || 0))
}

function normalizeForecastLevel(levelText) {
  const s = String(levelText || '').toLowerCase()
  if (s.includes('heavy') || s.includes('high')) return 'high'
  if (s.includes('moderate') || s.includes('medium')) return 'medium'
  return 'low'
}

function heatColor(intensity) {
  const i = Number(intensity || 0)
  if (i >= 0.75) return '#ef4444'
  if (i >= 0.45) return '#f59e0b'
  return '#22c55e'
}

function trafficLevelFromIntensity(intensity) {
  const i = Number(intensity || 0)
  if (i >= 0.75) return { label: 'Heavy', color: '#ef4444' }
  if (i >= 0.45) return { label: 'Moderate', color: '#f59e0b' }
  return { label: 'Light', color: '#22c55e' }
}

function shortPlaceName(label = '', fallback = '') {
  const text = String(label || fallback || '').trim()
  if (!text) return fallback || 'Location'
  return text.split(',')[0].trim() || fallback || 'Location'
}

function isLocationLike(label = '') {
  const text = String(label || '').toLowerCase().trim()
  if (!text) return false
  if (text.length < 5) return false
  const bad = ['closed', 'stationary', 'queue', 'queueing', 'roadworks', 'incident', 'traffic', 'congestion']
  if (bad.some((w) => text.includes(w))) return false
  const good = ['road', 'rd', 'street', 'st', 'ave', 'junction', 'circle', 'flyover', 'bridge', 'layout', 'market', 'nagar', 'colony', 'main', 'cross', 'block', 'sector', 'phase', 'extension', 'ring', 'mg']
  return good.some((w) => text.includes(w)) || text.split(' ').length >= 2
}

function toTitle(text = '') {
  return String(text || '')
    .split(' ')
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ')
}

function poiVisualKey(poi = '') {
  const p = String(poi || '').toLowerCase()
  if (p === 'hospital') return 'hospital'
  if (p === 'police') return 'police'
  if (p === 'fuel') return 'fuel'
  if (p === 'restaurant') return 'restaurant'
  if (p === 'hotel') return 'hotel'
  if (p === 'pharmacy') return 'pharmacy'
  if (p === 'atm') return 'atm'
  if (p === 'mall') return 'mall'
  if (p === 'metro') return 'metro'
  if (['college', 'university', 'school'].includes(p)) return 'education'
  return 'default'
}

function poiSuggestion(poi = '') {
  const p = String(poi || '').toLowerCase()
  if (!p) return 'Pick a POI to see nearby essentials tailored to your trip.'
  if (p === 'hospital') return 'In case of emergencies, the closest hospitals are listed below.'
  if (p === 'police') return 'Need assistance? Here are nearby police stations for quick access.'
  if (p === 'fuel') return 'Low on fuel? These stations are the nearest along your area.'
  if (p === 'restaurant') return 'Grab a bite nearby before you continue your journey.'
  if (p === 'hotel') return 'Looking to stay? These hotels are closest to you.'
  if (p === 'pharmacy') return 'Pharmacies nearby for quick medical supplies.'
  if (p === 'atm') return 'Need cash? The nearest ATMs are listed below.'
  if (p === 'mall') return 'Shopping or a break? These malls are nearby.'
  if (p === 'metro') return 'Planning public transit? Metro stations around you.'
  if (['college', 'university', 'school'].includes(p)) return 'Education options nearby if you need campus access.'
  return 'Nearby places are listed based on your selection.'
}

function aqiMeta(aqi) {
  const v = Number(aqi || 0)
  if (v === 1) return { label: 'Good', color: '#22c55e', cls: 'good' }
  if (v === 2) return { label: 'Fair', color: '#84cc16', cls: 'fair' }
  if (v === 3) return { label: 'Moderate', color: '#f59e0b', cls: 'moderate' }
  if (v === 4) return { label: 'Poor', color: '#f97316', cls: 'poor' }
  if (v === 5) return { label: 'Very Poor', color: '#ef4444', cls: 'very-poor' }
  return { label: 'Unavailable', color: '#94a3b8', cls: 'na' }
}

function buildIncidentQuery(lat, lon, spanKm = 6) {
  const latDelta = spanKm / 111
  const lonDelta = spanKm / (111 * Math.max(Math.cos((Number(lat || 0) * Math.PI) / 180), 0.2))
  const minLat = Number(lat) - latDelta
  const maxLat = Number(lat) + latDelta
  const minLon = Number(lon) - lonDelta
  const maxLon = Number(lon) + lonDelta
  return `/incidents?min_lon=${minLon}&min_lat=${minLat}&max_lon=${maxLon}&max_lat=${maxLat}`
}

function weatherIconClass(desc = '', main = '') {
  const s = `${main} ${desc}`.toLowerCase()
  if (s.includes('rain') || s.includes('drizzle')) return 'rainy'
  if (s.includes('storm') || s.includes('thunder')) return 'storm'
  if (s.includes('cloud')) return 'cloudy'
  if (s.includes('mist') || s.includes('haze') || s.includes('fog')) return 'mist'
  return 'sunny'
}

function pct01(v) {
  return Math.max(0, Math.min(100, Number(v) || 0))
}

function fallbackHeatspots() {
  const baseLat = 12.9716
  const baseLon = 77.5946
  const spots = []
  const offsets = [-0.06, -0.03, 0, 0.03, 0.06]
  let idx = 0
  for (const dLat of offsets) {
    for (const dLon of offsets) {
      const intensity = idx % 3 === 0 ? 0.8 : idx % 3 === 1 ? 0.55 : 0.35
      spots.push({
        lat: baseLat + dLat,
        lon: baseLon + dLon,
        intensity,
        label: 'Traffic hotspot',
        source: 'fallback',
      })
      idx += 1
    }
  }
  return spots
}

function GaugeRing({ label, value, max = 100, unit = '', color = '#0ea5e9' }) {
  const safeValue = Number.isFinite(value) ? value : 0
  const pct = Math.max(0, Math.min(100, (safeValue / max) * 100))
  const style = { background: `conic-gradient(${color} ${pct}%, #e2e8f0 ${pct}% 100%)` }

  return (
    <div className="gauge-wrap">
      <div className="gauge" style={style}>
        <div className="gauge-inner">
          <strong>{safeValue.toFixed(1)}</strong>
          <span>{unit}</span>
        </div>
      </div>
      <p>{label}</p>
    </div>
  )
}

function SkeletonBlock({ h = 16 }) {
  return <div className="skeleton" style={{ height: h }} />
}

async function apiGet(path, token = '') {
  const headers = token ? { Authorization: `Bearer ${token}` } : {}
  const res = await fetch(`${API_BASE}${path}`, { headers })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || `Failed: ${path}`)
  }
  return res.json()
}

async function apiPost(path, payload, token = '') {
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || `Failed: ${path}`)
  }
  return res.json()
}

function compactHistory(items = []) {
  return (items || []).slice(0, 6).map((h) => ({
    source: h.source,
    destination: h.destination,
    eta: h.eta,
    delay: h.delay,
    distance: h.distance,
    ts: h.ts,
  }))
}

export default function App() {
  // Demo mode skips auth and drops directly into the dashboard.
  const DEMO_MODE = false
  const [activePage, setActivePage] = useState(DEMO_MODE ? 'home' : 'login')
  const [isLoggedIn, setIsLoggedIn] = useState(DEMO_MODE)
  const [authToken, setAuthToken] = useState(() => localStorage.getItem('route_nova_token') || '')
  const [authMode, setAuthMode] = useState('signin')
  const [loginUser, setLoginUser] = useState('')
  const [signupEmail, setSignupEmail] = useState('')
  const [loginPass, setLoginPass] = useState('')
  const [signupPass2, setSignupPass2] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)
  const [loginError, setLoginError] = useState('')
  const [historyItems, setHistoryItems] = useState([])
  const [source, setSource] = useState('')
  const [destination, setDestination] = useState('')
  const [departHours, setDepartHours] = useState(1)
  const [departMinutesOnly, setDepartMinutesOnly] = useState(30)
  const [poiType, setPoiType] = useState('')

  const [sourceSuggestions, setSourceSuggestions] = useState([])
  const [destinationSuggestions, setDestinationSuggestions] = useState([])
  const [sourceFocused, setSourceFocused] = useState(false)
  const [destinationFocused, setDestinationFocused] = useState(false)

  const [routePlan, setRoutePlan] = useState(null)
  const [flow, setFlow] = useState(null)
  const [weather, setWeather] = useState(null)
  const [incidents, setIncidents] = useState(null)
  const [placesByType, setPlacesByType] = useState({ hospital: [], police: [], fuel: [] })
  const [liveHeatspots, setLiveHeatspots] = useState([])
  const [heatUpdatedAt, setHeatUpdatedAt] = useState('static')
  const [forecast, setForecast] = useState(null)
  const [trafficTemplate, setTrafficTemplate] = useState('')
  const [lastUpdated, setLastUpdated] = useState('')
  const [userLoc, setUserLoc] = useState({ lat: 12.9716, lon: 77.5946 })
  const [currentArea, setCurrentArea] = useState('Bengaluru')
  const [insideBangalore, setInsideBangalore] = useState(true)
  const [airQuality, setAirQuality] = useState(null)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selectedRouteIdx, setSelectedRouteIdx] = useState(0)
  const [showTrafficLayer, setShowTrafficLayer] = useState(true)
  const [trafficSegments, setTrafficSegments] = useState([])
  const [lastTrafficUpdate, setLastTrafficUpdate] = useState(null)
  const [trafficCountdown, setTrafficCountdown] = useState(45)
  const [hasRouteResult, setHasRouteResult] = useState(false)
  const [hasTrafficResult, setHasTrafficResult] = useState(false)
  const [lastAction, setLastAction] = useState('')
  const [aiInsights, setAiInsights] = useState({})
  const [aiInsightLoading, setAiInsightLoading] = useState({})
  const [trafficMapSummary, setTrafficMapSummary] = useState([])
  const [trafficMapSummaryLoading, setTrafficMapSummaryLoading] = useState(false)
  const [trafficMapSummaryError, setTrafficMapSummaryError] = useState(false)
  const [navOpen, setNavOpen] = useState(false)
  const [navSteps, setNavSteps] = useState([])
  const [navStepIndex, setNavStepIndex] = useState(0)
  const [navWatchId, setNavWatchId] = useState(null)
  const [navVoice, setNavVoice] = useState(false)
  const [navError, setNavError] = useState('')
  const CHAT_STORAGE_KEY = 'routenova_ai_chat_v1'
  const greetingMessage = {
    role: 'assistant',
    text: "Hello! I'm your RouteNova AI Assistant.\nI can help you with:\n- Best routes\n- Traffic conditions\n- Travel time suggestions\n- Weather impact on traffic\nAsk me anything!",
    ts: new Date().toISOString(),
  }
  const [aiChatMessages, setAiChatMessages] = useState(() => {
    try {
      const raw = localStorage.getItem(CHAT_STORAGE_KEY)
      const parsed = raw ? JSON.parse(raw) : null
      return Array.isArray(parsed) && parsed.length ? parsed : [greetingMessage]
    } catch {
      return [greetingMessage]
    }
  })
  const [aiChatInput, setAiChatInput] = useState('')
  const [aiChatLoading, setAiChatLoading] = useState(false)
  const [showAiAssistant, setShowAiAssistant] = useState(false)
  const [isListening, setIsListening] = useState(false)
  const [voiceEnabled, setVoiceEnabled] = useState(false)

  const isHome = activePage === 'home'
  const isRoutePlanPage = activePage === 'route-plan'
  const isTrafficMapPage = activePage === 'traffic-map'
  const isWeatherPage = activePage === 'weather'
  const isUrbanPage = activePage === 'urban-essentials'
  const aiContext = isRoutePlanPage
    ? 'route'
    : isTrafficMapPage
      ? 'traffic-map'
      : isWeatherPage
        ? 'weather'
        : isUrbanPage
          ? 'urban-essentials'
          : activePage === 'history'
            ? 'history'
            : 'home'

  const mapNodeRef = useRef(null)
  const heatMapNodeRef = useRef(null)
  const overviewMapNodeRef = useRef(null)
  const homeHeatMapNodeRef = useRef(null)
  const mapRef = useRef(null)
  const heatMapRef = useRef(null)
  const overviewMapRef = useRef(null)
  const homeHeatMapRef = useRef(null)
  const routeLayerRef = useRef(null)
  const routePolylineRef = useRef([])
  const trafficLinesRef = useRef(null)
  const trafficLayerRef = useRef(null)
  const heatMapLayerRef = useRef(null)
  const homeHeatMapLayerRef = useRef(null)
  const overviewHeatLayerRef = useRef(null)
  const overviewTrafficLayerRef = useRef(null)
  const sourceFieldRef = useRef(null)
  const destinationFieldRef = useRef(null)
  const homeHeatSignatureRef = useRef('')
  const lastDashboardCoordsRef = useRef({ lat: 12.9716, lon: 77.5946 })
  const overviewCurrentMarkerRef = useRef(null)
  const homeCurrentMarkerRef = useRef(null)
  const aiChatEndRef = useRef(null)

  const debouncedSource = useDebouncedValue(source)
  const debouncedDestination = useDebouncedValue(destination)
  const departInMinutes = useMemo(
    () => (Number(departHours || 0) * 60) + Number(departMinutesOnly || 0),
    [departHours, departMinutesOnly],
  )

  const alternativeRoutes = useMemo(
    () => ensureRealRoutes(routePlan?.routes || []),
    [routePlan?.routes],
  )
  const bestRoute = alternativeRoutes?.[0] || null
  const eta = Number(bestRoute?.travel_minutes || 0)
  const delay = Number(bestRoute?.traffic_delay_minutes || 0)
  const delayDisplay = Number.isFinite(delay)
    ? (delay <= 0 ? 'No congestion delay' : `${delay.toFixed(1)} min`)
    : '--'
  const delaySourceText = bestRoute?.delay_source === 'tomtom_trafficDelayInSeconds'
    ? 'Live delay from TomTom traffic.'
    : bestRoute?.delay_source === 'derived_travel_minus_noTraffic'
      ? 'Delay estimated from travel time vs no-traffic time.'
      : 'Delay currently estimated with limited live data.'
  const riskScore = (bestRoute?.route_score || 0) / 60
  const fallbackDelay = useMemo(
    () => Math.max(0, Number(flow?.travel_time_index || 1) - 1) * 10,
    [flow?.travel_time_index],
  )
  const computedBaseEta = eta > 0 ? Math.max(eta - delay, 1) : 30
  const computedDelay = eta > 0 ? Math.max(delay, 0) : fallbackDelay
  const etaTotal = Math.max(computedBaseEta + computedDelay, 1)
  const basePct = Math.min(100, (computedBaseEta / etaTotal) * 100)
  const delayPct = Math.min(100, (computedDelay / etaTotal) * 100)
  const homePeakDelay = Number.isFinite(computedDelay) ? `${computedDelay.toFixed(1)} min` : '--'
  const effectivePoi = poiType && poiType !== 'none' ? poiType : ''
  const selectedPlaces = placesByType?.[effectivePoi] || []
  const routeColors = ['#00FF7F', '#1E90FF', '#8A2BE2']
  const routeNames = ['Best Route', 'Alt 1', 'Alt 2']
  const bestTimeSaved = useMemo(() => {
    if (!alternativeRoutes.length) return 0
    const times = alternativeRoutes.map((r) => Number(r.travel_minutes || 0)).filter((t) => Number.isFinite(t) && t > 0)
    if (!times.length) return 0
    const best = Math.min(...times)
    const worst = Math.max(...times)
    return Math.max(0, Math.round((worst - best) * 10) / 10)
  }, [alternativeRoutes])
  const routeInsights = useMemo(() => {
    if (!alternativeRoutes.length) {
      return {
        fastest: '--',
        slowest: '--',
        timeSaved: 0,
        recommendation: 'Run a route prediction to view insights.',
      }
    }
    const times = alternativeRoutes.map((r, idx) => ({
      idx,
      time: Number(r.travel_minutes || 0),
    }))
    const sorted = [...times].sort((a, b) => a.time - b.time)
    const fastestIdx = sorted[0]?.idx ?? 0
    const slowestIdx = sorted[sorted.length - 1]?.idx ?? 0
    const timeSaved = Math.max(0, (sorted[sorted.length - 1]?.time || 0) - (sorted[0]?.time || 0))
    return {
      fastest: routeNames[fastestIdx] || `Alt ${fastestIdx}`,
      slowest: routeNames[slowestIdx] || `Alt ${slowestIdx}`,
      timeSaved: Math.round(timeSaved * 10) / 10,
      recommendation: `Take ${routeNames[fastestIdx] || 'Best Route'} for quickest arrival`,
    }
  }, [alternativeRoutes, routeNames])
  const trafficInsights = useMemo(() => {
    if (!trafficSegments.length) {
      return { avgSpeed: 0, congestion: 'No Data', worst: 'Bengaluru Core', delay: 0 }
    }
    const speeds = trafficSegments.map((s) => Number(s.speed || 0)).filter((s) => s > 0)
    const avg = speeds.length ? speeds.reduce((a, b) => a + b, 0) / speeds.length : 0
    const congestion = avg > 35 ? 'Low' : avg > 20 ? 'Medium' : 'High'
    const delay = avg > 35 ? 2 : avg > 20 ? 6 : 12
    return { avgSpeed: avg, congestion, worst: 'Silk Board Junction', delay }
  }, [trafficSegments])
  const trafficBreakdown = useMemo(() => {
    const counts = { smooth: 0, moderate: 0, heavy: 0, severe: 0 }
    trafficSegments.forEach((s) => {
      counts[trafficBucket(s.speed)] += 1
    })
    return counts
  }, [trafficSegments])
  const trafficTotal = Math.max(trafficSegments.length, 1)
  const trafficPct = useMemo(() => ({
    smooth: Math.round((trafficBreakdown.smooth / trafficTotal) * 100),
    moderate: Math.round((trafficBreakdown.moderate / trafficTotal) * 100),
    heavy: Math.round((trafficBreakdown.heavy / trafficTotal) * 100),
    severe: Math.round((trafficBreakdown.severe / trafficTotal) * 100),
  }), [trafficBreakdown, trafficTotal])
  const dominantTraffic = useMemo(() => {
    const entries = [
      { key: 'smooth', label: 'Light', desc: 'Smooth traffic', pct: trafficPct.smooth },
      { key: 'moderate', label: 'Moderate', desc: 'Slow moving', pct: trafficPct.moderate },
      { key: 'heavy', label: 'Heavy', desc: 'Congested', pct: trafficPct.heavy },
      { key: 'severe', label: 'Severe', desc: 'Highly congested', pct: trafficPct.severe },
    ]
    return entries.sort((a, b) => b.pct - a.pct)[0]
  }, [trafficPct])
  const topCongested = useMemo(() => {
    const names = [
      'Silk Board Junction',
      'KR Puram Bridge',
      'Marathahalli',
      'Hebbal Flyover',
      'Tin Factory',
      'Madiwala',
      'Indiranagar 100ft Road',
      'Outer Ring Road',
    ]
    return [...trafficSegments]
      .sort((a, b) => (a.speed || 0) - (b.speed || 0))
      .slice(0, 3)
      .map((seg, idx) => ({
        name: names[idx % names.length],
        speed: seg.speed,
        level: getTrafficLabel(seg.speed),
      }))
  }, [trafficSegments])
  const weatherClass = useMemo(() => weatherIconClass(weather?.description, weather?.weather), [weather?.description, weather?.weather])
  useEffect(() => {
    const cls = `theme-${weatherClass || 'sunny'}`
    document.body.classList.remove('theme-sunny', 'theme-cloudy', 'theme-rainy', 'theme-storm', 'theme-mist')
    document.body.classList.add(cls)
    return () => {
      document.body.classList.remove(cls)
    }
  }, [weatherClass])
  const weatherHighlights = useMemo(() => {
    const temp = Number(weather?.temperature_c || 0)
    const feels = Number(weather?.feels_like_c || temp || 0)
    const hum = Number(weather?.humidity || 0)
    const wind = Number(weather?.wind_speed || 0)
    const comfort = hum && temp ? Math.max(0, Math.min(100, 100 - Math.abs(temp - 24) * 3 - Math.max(0, hum - 60))) : 0
    const windLevel = wind >= 8 ? 'Windy' : wind >= 4 ? 'Breezy' : 'Calm'
    const rainRisk = `${weather?.description || ''}`.toLowerCase().includes('rain') ? 'High' : hum >= 80 ? 'Medium' : 'Low'
    const travelTip = rainRisk === 'High'
      ? 'Carry rain gear and expect slower traffic.'
      : temp >= 32
        ? 'Stay hydrated and avoid long stops in heat.'
        : 'Weather is comfortable for travel.'
    return {
      comfort: comfort ? Math.round(comfort) : '--',
      windLevel,
      rainRisk,
      travelTip,
      feels: Number.isFinite(feels) ? feels.toFixed(1) : '--',
      dew: hum ? Math.round((temp - ((100 - hum) / 5)) * 10) / 10 : '--',
    }
  }, [weather])
  const liveDashboardHeatspots = useMemo(
    () => (liveHeatspots || []).filter((h) => ['tomtom', 'tomtom-flow'].includes(String(h?.source || '')) && Number.isFinite(Number(h?.lat)) && Number.isFinite(Number(h?.lon))),
    [liveHeatspots],
  )
  const displayHeatspots = useMemo(
    () => (liveHeatspots || []).filter((h) => Number.isFinite(Number(h?.lat)) && Number.isFinite(Number(h?.lon))),
    [liveHeatspots],
  )
  const heatspotsForGauge = useMemo(() => {
    if ((displayHeatspots || []).length) return displayHeatspots
    if ((liveHeatspots || []).length) return liveHeatspots
    return fallbackHeatspots()
  }, [displayHeatspots, liveHeatspots])

  const dashboardIncidentItems = useMemo(() => {
    if (liveDashboardHeatspots.length) {
      return liveDashboardHeatspots.map((spot) => ({
        delay_seconds: spot.delay_seconds || 0,
      }))
    }
    return incidents?.incidents || []
  }, [liveDashboardHeatspots, incidents])
  const incidentSeverity = useMemo(() => {
    const out = { high: 0, medium: 0, low: 0 }
    dashboardIncidentItems.forEach((it) => {
      const sev = severityFromDelay(it.delay_seconds).label.toLowerCase()
      if (sev in out) out[sev] += 1
    })
    return out
  }, [dashboardIncidentItems])
  const focusedTrafficSpots = useMemo(() => {
    const sourcePoint = routePlan?.source
    const destinationPoint = routePlan?.destination
    const routeLine = normalizePolyline(bestRoute?.polyline)
    const spots = (displayHeatspots || []).filter((spot) => spot?.lat && spot?.lon)

    if (!sourcePoint?.lat || !sourcePoint?.lon || !destinationPoint?.lat || !destinationPoint?.lon) {
      return spots
        .sort((a, b) => Number(b.intensity || 0) - Number(a.intensity || 0))
        .slice(0, 8)
    }

    const scored = spots.map((spot) => {
      const distToSource = haversineKm(spot.lat, spot.lon, sourcePoint.lat, sourcePoint.lon)
      const distToDestination = haversineKm(spot.lat, spot.lon, destinationPoint.lat, destinationPoint.lon)
      const distToRoute = routeLine.length
        ? pointToPolylineDistanceKm(spot.lat, spot.lon, routeLine)
        : Math.min(distToSource, distToDestination)
      return {
        ...spot,
        distToSource,
        distToDestination,
        distToRoute,
      }
    })

    const withinTrip = scored
      .filter((spot) => spot.distToRoute <= 1.4 || spot.distToSource <= 2 || spot.distToDestination <= 2)
      .sort((a, b) => {
        if (a.distToRoute !== b.distToRoute) return a.distToRoute - b.distToRoute
        return Number(b.intensity || 0) - Number(a.intensity || 0)
      })

    if (withinTrip.length) return withinTrip.slice(0, 8)

    return scored
      .sort((a, b) => {
        const aBest = Math.min(a.distToSource, a.distToDestination, a.distToRoute)
        const bBest = Math.min(b.distToSource, b.distToDestination, b.distToRoute)
        if (aBest !== bBest) return aBest - bBest
        return Number(b.intensity || 0) - Number(a.intensity || 0)
      })
      .slice(0, 8)
  }, [displayHeatspots, routePlan?.source, routePlan?.destination, bestRoute?.polyline])
  const homeNearbyHeatspots = useMemo(() => {
    return [...displayHeatspots]
      .map((spot) => ({
        ...spot,
        distance: haversineKm(userLoc.lat, userLoc.lon, Number(spot.lat), Number(spot.lon)),
      }))
      .sort((a, b) => a.distance - b.distance)
      .slice(0, 20)
  }, [displayHeatspots, userLoc])
  const totalIncidents = liveDashboardHeatspots.length || Number(incidents?.count || 0)
  const highPct = totalIncidents ? (incidentSeverity.high / totalIncidents) * 100 : 8
  const mediumPct = totalIncidents ? (incidentSeverity.medium / totalIncidents) * 100 : 8
  const lowPct = totalIncidents ? (incidentSeverity.low / totalIncidents) * 100 : 8

  const currentAreaLabel = useMemo(() => {
    const area = String(currentArea || 'Bengaluru').trim()
    if (!area) return 'Current Area: Location'
    if (area.toLowerCase().includes('bengaluru')) return `Current Area: ${area}`
    if (insideBangalore) return `Current Area: ${area}, Bengaluru`
    return `Current Area: ${area}`
  }, [currentArea, insideBangalore])


  const requestAiInsight = async (context) => {
    setAiInsightLoading((prev) => ({ ...prev, [context]: true }))
    try {
      const payload = buildAiPayload(context)
      const res = await apiPost('/ai-insights', payload)
      setAiInsights((prev) => ({ ...prev, [context]: res.insight || 'AI insight unavailable.' }))
    } catch {
      setAiInsights((prev) => ({ ...prev, [context]: 'AI service unavailable.' }))
    } finally {
      setAiInsightLoading((prev) => ({ ...prev, [context]: false }))
    }
  }

  const sendAiMessage = async (text, intent = '', short = false) => {
    const base = String(text || '').trim()
    const msg = intent ? `Intent: ${intent}. Question: ${base}${short ? ' Reply in 1-2 lines only.' : ''}` : base
    if (!msg || aiChatLoading) return
    const nowIso = new Date().toISOString()
    setAiChatInput('')
    setAiChatMessages((prev) => [...prev, { role: 'user', text: base, ts: nowIso }])
    setAiChatLoading(true)

    const localFallback = () => {
      if (intent === 'best_route_now' && bestRoute) {
        return `Best route ETA ${bestRoute.travel_minutes} min, delay ${bestRoute.traffic_delay_minutes} min.`
      }
      if (intent === 'traffic_near_me') {
        return `Traffic ${homeTrafficState.label}. Speed ${currentTrafficSpeedText}.`
      }
      if (intent === 'best_travel_time_today') {
        return `Best time is off-peak; current traffic is ${homeTrafficState.label}.`
      }
      if (intent === 'weather_impact') {
        return `Weather ${weather?.description || weather?.weather || 'unknown'} may affect travel times.`
      }
      return 'Please compute a route first to get accurate suggestions.'
    }
    try {
      const payload = {
        context: aiContext,
        message: msg,
        snapshot: buildAiPayload(aiContext),
        intent,
      }
      const res = await fetch(`${API_BASE}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).then((r) => r.json())
      let replyText = res?.reply || ''
      if (!replyText || replyText.toLowerCase().includes('refine your query')) {
        replyText = localFallback()
      }
      setAiChatMessages((prev) => [...prev, { role: 'assistant', text: replyText, ts: new Date().toISOString() }])
      if (voiceEnabled && 'speechSynthesis' in window) {
        const utter = new SpeechSynthesisUtterance(replyText)
        window.speechSynthesis.speak(utter)
      }
    } catch {
      setAiChatMessages((prev) => [...prev, { role: 'assistant', text: localFallback(), ts: new Date().toISOString() }])
    } finally {
      setAiChatLoading(false)
    }
  }

  const startListening = () => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SpeechRecognition || isListening) return
    const recognition = new SpeechRecognition()
    recognition.lang = 'en-IN'
    recognition.interimResults = false
    recognition.maxAlternatives = 1
    setIsListening(true)
    recognition.onresult = (event) => {
      const transcript = event.results?.[0]?.[0]?.transcript || ''
      setAiChatInput(transcript)
    }
    recognition.onend = () => {
      setIsListening(false)
    }
    recognition.onerror = () => {
      setIsListening(false)
    }
    recognition.start()
  }

  const sendAiChat = async () => {
    await sendAiMessage(aiChatInput)
  }

  const pushMissingData = () => {
    setAiChatMessages((prev) => [...prev, { role: 'assistant', text: 'Please compute route or load traffic data first.', ts: new Date().toISOString() }])
  }

  const handleQuickBestRoute = () => {
    const query = 'Suggest the best route based on current traffic and conditions.'
    console.log('[AI Quick Action] Best route now:', query)
    if (!bestRoute) {
      pushMissingData()
      return
    }
    sendAiMessage(query, 'best_route_now', true)
  }

  const handleQuickTrafficNear = () => {
    const query = 'Provide current traffic conditions and congestion level for the selected location.'
    console.log('[AI Quick Action] Traffic near me:', query)
    const hasTrafficData = Boolean(flow || incidents || (liveHeatspots || []).length)
    if (!hasTrafficData) {
      pushMissingData()
      return
    }
    sendAiMessage(query, 'traffic_near_me', true)
  }

  const handleQuickBestTime = () => {
    const query = 'Suggest the best time to travel today considering traffic patterns.'
    console.log('[AI Quick Action] Best travel time today:', query)
    const hasTrafficData = Boolean(flow || incidents || (liveHeatspots || []).length)
    if (!hasTrafficData) {
      pushMissingData()
      return
    }
    sendAiMessage(query, 'best_travel_time_today', true)
  }

  const handleQuickWeatherImpact = () => {
    const query = 'Explain how current weather conditions affect traffic and travel.'
    console.log('[AI Quick Action] Weather impact:', query)
    if (!weather) {
      pushMissingData()
      return
    }
    sendAiMessage(query, 'weather_impact', true)
  }

  const clearAiChat = () => {
    setAiChatMessages([greetingMessage])
    try {
      localStorage.removeItem(CHAT_STORAGE_KEY)
    } catch {
      // ignore
    }
  }

  const loadDashboard = async (lat, lon, area = 'Bengaluru') => {
    console.log('Loading dashboard for:', lat, lon, area)
    const prev = lastDashboardCoordsRef.current || {}
    const latChanged = Math.abs(Number(prev.lat || 0) - Number(lat || 0)) > 0.0005
    const lonChanged = Math.abs(Number(prev.lon || 0) - Number(lon || 0)) > 0.0005
    if (latChanged || lonChanged) {
      lastDashboardCoordsRef.current = { lat, lon }
      setUserLoc({ lat, lon })
    }

    try {
      const context = await apiGet(`/location-context?lat=${lat}&lon=${lon}`).catch(() => ({ area }))
      const areaName = context?.area || area || 'Bengaluru'
      setCurrentArea(areaName)
      setInsideBangalore(context?.inside_bangalore ?? true)
      const [weatherRes, flowRes, incidentsRes, forecastRes, airRes] = await Promise.allSettled([
        apiGet(`/weather?lat=${lat}&lon=${lon}`),
        apiGet(`/traffic-flow?lat=${lat}&lon=${lon}`),
        apiGet(buildIncidentQuery(lat, lon)),
        apiGet(`/traffic-forecast?source_area=${encodeURIComponent(areaName)}`),
        apiGet(`/air-quality?lat=${lat}&lon=${lon}`),
      ])

      setWeather(weatherRes.status === 'fulfilled' ? weatherRes.value : null)
      setFlow(flowRes.status === 'fulfilled' ? flowRes.value : null)
      setIncidents(incidentsRes.status === 'fulfilled' ? incidentsRes.value : null)
      setForecast(forecastRes.status === 'fulfilled' ? forecastRes.value : { cards: [], trend: [], source: 'model' })
      setAirQuality(airRes.status === 'fulfilled' ? airRes.value : null)
    } catch (err) {
      console.error('Dashboard load error:', err)
    }

    setLastUpdated(new Date().toLocaleString())
  }
  useEffect(() => {
    if (DEMO_MODE) {
      setIsLoggedIn(true)
      if (!loginUser) setLoginUser('Guest')
      if (activePage === 'login') setActivePage('home')
      return
    }
    if (!authToken) return
    apiGet('/auth/me', authToken)
      .then((d) => {
        if (d?.user?.username) {
          setIsLoggedIn(true)
          setLoginUser(d.user.username)
          if (activePage === 'login') setActivePage('home')
        }
      })
      .catch(() => {
        localStorage.removeItem('route_nova_token')
        setAuthToken('')
        setIsLoggedIn(false)
        setActivePage('login')
      })
  }, [authToken, activePage, loginUser])


  useEffect(() => {
    if (!authToken || !isLoggedIn) return
    apiGet('/history', authToken)
      .then((d) => setHistoryItems(d.items || []))
      .catch(() => setHistoryItems([]))
  }, [authToken, isLoggedIn])

  useEffect(() => {
    if (!isLoggedIn || activePage !== 'home') return

    // Load default Bangalore data immediately
    lastDashboardCoordsRef.current = { lat: 12.9716, lon: 77.5946 }
    loadDashboard(12.9716, 77.5946)

    // Then attempt to refine with user location
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (pos) => loadDashboard(pos.coords.latitude, pos.coords.longitude),
        () => {
          apiGet('/current-location')
            .then((loc) => loadDashboard(loc.lat || 12.9716, loc.lon || 77.5946, loc.city || 'Bengaluru'))
            .catch((err) => console.warn('Current location fallback failed:', err.message))
        },
        { timeout: 8000 }
      )
    } else {
      apiGet('/current-location')
        .then((loc) => loadDashboard(loc.lat || 12.9716, loc.lon || 77.5946, loc.city || 'Bengaluru'))
        .catch((err) => console.warn('Current location fallback failed:', err.message))
    }

    const id = setInterval(() => {
      const nextLat = userLoc?.lat || 12.9716
      const nextLon = userLoc?.lon || 77.5946
      loadDashboard(nextLat, nextLon)
    }, 45000)

    return () => clearInterval(id)
  }, [isLoggedIn, activePage, userLoc?.lat, userLoc?.lon])

  useEffect(() => {
    if (activePage !== 'route-plan' && activePage !== 'traffic-map') return
    if (!mapNodeRef.current || mapRef.current) return
    const map = L.map(mapNodeRef.current, {
      zoomControl: true,
      maxBounds: [[12.82, 77.38], [13.16, 77.82]],
      maxBoundsViscosity: 1.0,
    }).setView([12.9716, 77.5946], 11)

    map.createPane('hotspots')
    map.getPane('hotspots').style.zIndex = 500

    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      minZoom: 10,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map)

    routeLayerRef.current = L.layerGroup().addTo(map)
    mapRef.current = map
    setTimeout(() => map.invalidateSize(), 0)

    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [activePage])

  useEffect(() => {
    if (activePage !== 'route-plan' && activePage !== 'traffic-map') return
    if (!mapRef.current) return
    const id = setTimeout(() => {
      mapRef.current?.invalidateSize()
    }, 0)
    return () => clearTimeout(id)
  }, [activePage])

  useEffect(() => {
    if (activePage !== 'home') return
    if (!overviewMapNodeRef.current || overviewMapRef.current) return
    const map = L.map(overviewMapNodeRef.current, {
      zoomControl: true,
      attributionControl: false,
      dragging: true,
      scrollWheelZoom: true,
      doubleClickZoom: true,
      boxZoom: true,
      keyboard: true,
      maxBounds: [[12.82, 77.38], [13.16, 77.82]],
      maxBoundsViscosity: 1.0,
      minZoom: 11,
      maxZoom: 15,
    }).setView([userLoc.lat, userLoc.lon], 12)

    map.createPane('hotspots')
    map.getPane('hotspots').style.zIndex = 500

    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 15,
      minZoom: 11,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map)

    const handleMapClick = async (evt) => {
      const { lat, lng } = evt.latlng || {}
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) return
      try {
        const ctx = await apiGet(`/location-context?lat=${lat}&lon=${lng}`)
        const areaName = ctx?.area || 'Bengaluru'
        loadDashboard(lat, lng, areaName)
        L.popup()
          .setLatLng([lat, lng])
          .setContent(`<strong>${areaName}</strong>`)
          .openOn(map)
      } catch {
        L.popup()
          .setLatLng([lat, lng])
          .setContent('<strong>Area unavailable</strong>')
          .openOn(map)
      }
    }
    map.on('click', handleMapClick)

    overviewHeatLayerRef.current = L.layerGroup().addTo(map)
    overviewMapRef.current = map
    return () => {
      map.off('click', handleMapClick)
      map.remove()
      overviewHeatLayerRef.current = null
      overviewTrafficLayerRef.current = null
      overviewMapRef.current = null
    }
  }, [activePage, userLoc])

  useEffect(() => {
    if (activePage !== 'home') return
    if (!homeHeatMapNodeRef.current || homeHeatMapRef.current) return
    const map = L.map(homeHeatMapNodeRef.current, {
      zoomControl: false,
      attributionControl: false,
      dragging: false,
      scrollWheelZoom: false,
      doubleClickZoom: false,
      boxZoom: false,
      keyboard: false,
      maxBounds: [[12.82, 77.38], [13.16, 77.82]],
      maxBoundsViscosity: 1.0,
    }).setView([userLoc.lat, userLoc.lon], 12)

    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      minZoom: 10,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map)
    homeHeatMapLayerRef.current = L.layerGroup().addTo(map)
    homeHeatMapRef.current = map

    return () => {
      map.remove()
      homeHeatMapRef.current = null
      homeHeatMapLayerRef.current = null
    }
  }, [activePage, userLoc])

  useEffect(() => {
    if (!heatMapNodeRef.current || heatMapRef.current) return
    const map = L.map(heatMapNodeRef.current, {
      zoomControl: false,
      maxBounds: [[12.82, 77.38], [13.16, 77.82]],
      maxBoundsViscosity: 1.0,
      minZoom: 11,
      maxZoom: 15,
    }).setView([12.9716, 77.5946], 11)

    map.createPane('hotspots')
    map.getPane('hotspots').style.zIndex = 500

    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
      minZoom: 10,
    }).addTo(map)
    heatMapLayerRef.current = L.layerGroup().addTo(map)
    heatMapRef.current = map

    return () => {
      map.remove()
      heatMapRef.current = null
    }
  }, [])

  useEffect(() => {
    apiGet('/traffic-tiles-template').then((d) => setTrafficTemplate(d.url_template || '')).catch(() => null)

    const fetchHeatspotsOnce = (attempt = 0) => {
      apiGet('/live-heatspots?limit=500')
        .then((d) => {
          const nextHeatspots = d.heatspots || []
          const signature = JSON.stringify(nextHeatspots.slice(0, 40).map((h) => [h.label, h.lat, h.lon, h.intensity, h.delay_seconds, h.source]))
          if (signature !== homeHeatSignatureRef.current) {
            homeHeatSignatureRef.current = signature
            setLiveHeatspots(nextHeatspots)
          }
          setHeatUpdatedAt(d.updated_at || 'static')
        })
        .catch(() => {
          if (attempt < 3) {
            setTimeout(() => fetchHeatspotsOnce(attempt + 1), 2500)
          }
        })
    }

    fetchHeatspotsOnce()
  }, [])

  // Keep model-based hotspots static after initial load.

  useEffect(() => {
    if (!showTrafficLayer) return
    const refresh = () => {
      setTrafficSegments(generateMockTrafficSegments(userLoc, 140))
      setLastTrafficUpdate(new Date())
      setTrafficCountdown(45)
    }
    refresh()
    const id = setInterval(refresh, 45000)
    return () => clearInterval(id)
  }, [showTrafficLayer, userLoc])

  useEffect(() => {
    if (!showTrafficLayer) return
    const id = setInterval(() => {
      setTrafficCountdown((prev) => (prev > 0 ? prev - 1 : 0))
    }, 1000)
    return () => clearInterval(id)
  }, [showTrafficLayer])


  useEffect(() => {
    if (!showAiAssistant) return
    const id = setTimeout(() => {
      aiChatEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }, 0)
    return () => clearTimeout(id)
  }, [aiChatMessages, showAiAssistant])

  useEffect(() => {
    if (!navOpen) return
    if (!navigator.geolocation) return
    const id = navigator.geolocation.watchPosition(
      (pos) => {
        const { latitude, longitude } = pos.coords || {}
        if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return
        const current = navSteps[navStepIndex]
        if (current?.lat != null && current?.lon != null) {
          const distKm = haversineKm(latitude, longitude, current.lat, current.lon)
          if (distKm < 0.06 && navStepIndex < navSteps.length - 1) {
            const nextIdx = navStepIndex + 1
            setNavStepIndex(nextIdx)
            if (navVoice && 'speechSynthesis' in window) {
              const utter = new SpeechSynthesisUtterance(navSteps[nextIdx]?.text || '')
              window.speechSynthesis.speak(utter)
            }
          }
        }
      },
      () => null,
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 8000 },
    )
    setNavWatchId(id)
    return () => {
      if (id != null) navigator.geolocation.clearWatch(id)
    }
  }, [navOpen, navSteps, navStepIndex, navVoice])

  useEffect(() => {
    try {
      localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(aiChatMessages))
    } catch {
      // ignore storage errors
    }
  }, [aiChatMessages, CHAT_STORAGE_KEY])

  useEffect(() => {
    const applyHeat = (layer) => {
      if (!layer) return
      layer.clearLayers()
      ;(displayHeatspots || []).forEach((h) => {
        if (!h.lat || !h.lon) return
        const intensity = Number(h.intensity || 0.3)
        const color = heatColor(intensity)
        const glowColor = color
        const baseRadius = 6 + intensity * 10
        const baseOpacity = 0.95
        L.circleMarker([h.lat, h.lon], {
          radius: baseRadius,
          color,
          weight: 1.2,
          fillColor: color,
          fillOpacity: baseOpacity,
          pane: 'hotspots',
        }).addTo(layer).bindPopup(
          `<strong>${h.label || 'Traffic point'}</strong><br/>${Number(h.delay_seconds || 0) > 0 ? `${Math.round(Number(h.delay_seconds) / 60)} min delay` : 'Traffic update'}`
        )
      })
    }
    applyHeat(heatMapLayerRef.current)
  }, [displayHeatspots])

  useEffect(() => {
    const updateCurrentMarker = (map, markerRef, label, isLarge = false) => {
      if (!map || !userLoc?.lat || !userLoc?.lon) return
      if (markerRef.current) {
        map.removeLayer(markerRef.current)
        markerRef.current = null
      }
      const icon = L.divIcon({
        className: 'traffic-label-marker',
        html: `<div class="traffic-label location${isLarge ? ' big' : ''}">You</div>`,
        iconSize: isLarge ? [44, 44] : [36, 36],
        iconAnchor: isLarge ? [22, 22] : [18, 18],
      })
      markerRef.current = L.marker([userLoc.lat, userLoc.lon], { icon })
        .addTo(map)
        .bindPopup(`<strong>${label}</strong><br/>${userLoc.lat.toFixed(4)}, ${userLoc.lon.toFixed(4)}`)
    }

    const areaLabel = currentAreaLabel.replace('Current Area: ', '') || 'Current location'
    updateCurrentMarker(overviewMapRef.current, overviewCurrentMarkerRef, areaLabel, true)
    updateCurrentMarker(homeHeatMapRef.current, homeCurrentMarkerRef, areaLabel, true)

    const map = overviewMapRef.current
    if (!map || activePage !== 'home') return

    const nearby = (homeNearbyHeatspots || []).slice(0, 12)

    const bounds = L.latLngBounds([[userLoc.lat, userLoc.lon]])
    nearby.forEach((spot) => bounds.extend([Number(spot.lat), Number(spot.lon)]))
    if (bounds.isValid()) {
      map.fitBounds(bounds.pad(0.18))
    } else {
      map.setView([userLoc.lat, userLoc.lon], 12)
    }
  }, [activePage, homeNearbyHeatspots, userLoc, currentAreaLabel])

  useEffect(() => {
    const map = overviewMapRef.current
    if (!map || !trafficTemplate) return
    if (overviewTrafficLayerRef.current) {
      map.removeLayer(overviewTrafficLayerRef.current)
      overviewTrafficLayerRef.current = null
    }
    overviewTrafficLayerRef.current = L.tileLayer(trafficTemplate, { opacity: 0.5, maxZoom: 19 }).addTo(map)
  }, [trafficTemplate])

  useEffect(() => {
    if (!mapRef.current || !routeLayerRef.current) return
    const map = mapRef.current
    const layer = routeLayerRef.current
    layer.clearLayers()
    routePolylineRef.current = []

    if (trafficLayerRef.current) {
      map.removeLayer(trafficLayerRef.current)
      trafficLayerRef.current = null
    }

    if (trafficLinesRef.current) {
      map.removeLayer(trafficLinesRef.current)
      trafficLinesRef.current = null
    }

    if (isTrafficMapPage) {
      if (showTrafficLayer && trafficTemplate) {
        trafficLayerRef.current = L.tileLayer(trafficTemplate, { opacity: 0.35, maxZoom: 19 }).addTo(map)
      }

      const trafficGroup = L.layerGroup().addTo(map)
      trafficLinesRef.current = trafficGroup

      let trafficBounds = null
      if (routePlan?.source?.lat && routePlan?.source?.lon) {
        const sourceIcon = L.divIcon({
          className: 'traffic-label-marker',
          html: '<div class="traffic-label location">S</div>',
          iconSize: [28, 28],
          iconAnchor: [14, 14],
        })
        L.marker([routePlan.source.lat, routePlan.source.lon], { icon: sourceIcon })
          .addTo(trafficGroup)
          .bindPopup(`<strong>Source</strong><br/>${shortPlaceName(routePlan.source.label, 'Start')}`)
        trafficBounds = L.latLngBounds([[routePlan.source.lat, routePlan.source.lon]])
      }

      if (routePlan?.destination?.lat && routePlan?.destination?.lon) {
        const destinationIcon = L.divIcon({
          className: 'traffic-label-marker',
          html: '<div class="traffic-label destination">D</div>',
          iconSize: [28, 28],
          iconAnchor: [14, 14],
        })
        L.marker([routePlan.destination.lat, routePlan.destination.lon], { icon: destinationIcon })
          .addTo(trafficGroup)
          .bindPopup(`<strong>Destination</strong><br/>${shortPlaceName(routePlan.destination.label, 'End')}`)
        const destBounds = L.latLngBounds([[routePlan.destination.lat, routePlan.destination.lon]])
        if (!trafficBounds) trafficBounds = destBounds
        else trafficBounds.extend(destBounds)
      }

      if (bestRoute?.polyline && Array.isArray(bestRoute.polyline)) {
        const bestLine = normalizePolyline(bestRoute.polyline)
        if (bestLine.length >= 2) {
          const routeColor = getTrafficColor(Number(flow?.current_speed_kmph || 35))
          const bestPoly = L.polyline(bestLine, {
            color: routeColor,
            weight: 8,
            opacity: 1,
            lineCap: 'round',
            lineJoin: 'round',
          }).addTo(trafficGroup)
          // Add a soft glow to make the route stand out.
          L.polyline(bestLine, {
            color: '#ffffff',
            weight: 14,
            opacity: 0.18,
            lineCap: 'round',
            lineJoin: 'round',
          }).addTo(trafficGroup)
          trafficBounds = trafficBounds ? trafficBounds.extend(bestPoly.getBounds()) : bestPoly.getBounds()
        }
      }

      // No hotspots on traffic map - keep only best route

      if (trafficBounds?.isValid()) {
        map.fitBounds(trafficBounds.pad(0.18))
      } else if (userLoc?.lat && userLoc?.lon) {
        map.setView([userLoc.lat, userLoc.lon], 12)
      }
      return
    }

    if (!routePlan) return
    if (routePlan.source?.lat && routePlan.source?.lon) {
      const startIcon = L.divIcon({
        className: 'route-marker start',
        html: '<span class="marker-dot"></span>',
        iconSize: [18, 18],
      })
      L.marker([routePlan.source.lat, routePlan.source.lon], { icon: startIcon }).addTo(layer)
    }
    if (routePlan.destination?.lat && routePlan.destination?.lon) {
      const endIcon = L.divIcon({
        className: 'route-marker end',
        html: '<span class="marker-dot"></span>',
        iconSize: [18, 18],
      })
      L.marker([routePlan.destination.lat, routePlan.destination.lon], { icon: endIcon }).addTo(layer)
    }

    let bounds = null
    const routes = alternativeRoutes
    console.log('routes', routes)
    ;(routes || []).forEach((r, idx) => {
      const hasPolyline = Array.isArray(r.polyline) && r.polyline.length >= 2
      const isBest = idx === 0
      const fallbackLine = (() => {
        const s = routePlan?.source
        const d = routePlan?.destination
        if (!s?.lat || !s?.lon || !d?.lat || !d?.lon) return []
        const offset = idx === 0 ? 0 : 0.001 + idx * 0.0006
        return [
          [s.lat + offset, s.lon - offset],
          [d.lat + offset, d.lon - offset],
        ]
      })()
      const baseLineRaw = hasPolyline ? r.polyline : fallbackLine
      const baseLine = normalizePolyline(baseLineRaw)
      if (!Array.isArray(baseLine) || baseLine.length < 2) return
      const displayLine = baseLine
      const baseStyle = {
        color: routeColors[idx % routeColors.length],
        weight: isBest ? 5 : 4,
        opacity: 0.9,
        lineCap: 'round',
        lineJoin: 'round',
      }
      const poly = L.polyline(displayLine, {
        ...baseStyle,
      }).addTo(layer)
      routePolylineRef.current[idx] = poly
      poly.on('mouseover', () => {
        poly.setStyle({ weight: 6, opacity: 1 })
      })
      poly.on('click', () => {
        setSelectedRouteIdx(idx)
      })
      poly.on('mouseout', () => {
        const activeIdx = selectedRouteIdx ?? 0
        if (activeIdx === idx) {
          poly.setStyle({ weight: 6, opacity: 1 })
        } else {
          poly.setStyle(baseStyle)
        }
      })
      if (!bounds) bounds = poly.getBounds()
      else bounds.extend(poly.getBounds())
    })

    if (bounds) map.fitBounds(bounds.pad(0.16))
  }, [routePlan, trafficTemplate, alternativeRoutes, selectedRouteIdx, showTrafficLayer, trafficSegments, isTrafficMapPage, userLoc, focusedTrafficSpots])

  useEffect(() => {
    if (!routePolylineRef.current.length) return
    routePolylineRef.current.forEach((poly, idx) => {
      if (!poly) return
      const isBest = idx === 0
      if (idx === selectedRouteIdx) {
        poly.setStyle({ weight: isBest ? 6 : 5, opacity: 1 })
      } else {
        poly.setStyle({
          weight: isBest ? 5 : 4,
          opacity: 0.9,
        })
      }
    })
  }, [selectedRouteIdx])

  useEffect(() => {
    if (debouncedSource.trim().length < 1) {
      setSourceSuggestions([])
      return
    }
    apiGet(`/autocomplete?text=${encodeURIComponent(debouncedSource)}`)
      .then((d) => setSourceSuggestions(d.suggestions || []))
      .catch(() => setSourceSuggestions([]))
  }, [debouncedSource])

  useEffect(() => {
    if (debouncedDestination.trim().length < 1) {
      setDestinationSuggestions([])
      return
    }
    apiGet(`/autocomplete?text=${encodeURIComponent(debouncedDestination)}`)
      .then((d) => setDestinationSuggestions(d.suggestions || []))
      .catch(() => setDestinationSuggestions([]))
  }, [debouncedDestination])

  const canSubmit = useMemo(() => source.trim().length > 2 && destination.trim().length > 2, [source, destination])

  useEffect(() => {
    if (activePage !== 'traffic-map') return
    if (routePlan || loading || !canSubmit) return
    runPlan({ preventDefault: () => {} }, 'traffic-map')
  }, [activePage, routePlan, loading, canSubmit])

  useEffect(() => {
    if (alternativeRoutes.length) setSelectedRouteIdx(0)
  }, [alternativeRoutes.length])

  useEffect(() => {
    const onDocDown = (e) => {
      if (sourceFieldRef.current && !sourceFieldRef.current.contains(e.target)) {
        setSourceFocused(false)
      }
      if (destinationFieldRef.current && !destinationFieldRef.current.contains(e.target)) {
        setDestinationFocused(false)
      }
    }
    document.addEventListener('mousedown', onDocDown)
    return () => document.removeEventListener('mousedown', onDocDown)
  }, [])

  const trafficState = useMemo(() => {
    const tti = Number(flow?.travel_time_index || 1)
    const speed = Number(flow?.current_speed_kmph || 0)
    const delayMin = Number(bestRoute?.traffic_delay_minutes || 0)
    const incidentCount = Number(incidents?.count || 0)
    const fLevel = normalizeForecastLevel(forecast?.cards?.[0]?.level)

    const ttiNorm = clamp01((tti - 1) / 2)
    const delayNorm = clamp01(delayMin / 30)
    const speedNorm = speed > 0 ? clamp01((35 - speed) / 35) : 0
    const incidentNorm = clamp01(incidentCount / 10)
    const forecastBias = fLevel === 'high' ? 0.1 : fLevel === 'medium' ? 0.05 : 0
    const score = clamp01(ttiNorm * 0.35 + delayNorm * 0.3 + speedNorm * 0.2 + incidentNorm * 0.15 + forecastBias) * 100

    if (score >= 67) return { label: 'Heavy', cls: 'heavy', score, advice: 'Expect delays. Prefer alternate routes now.' }
    if (score >= 40) return { label: 'Moderate', cls: 'moderate', score, advice: 'Manageable traffic with moderate slowdown.' }
    return { label: 'Light', cls: 'light', score, advice: 'Road network is mostly clear.' }
  }, [flow, bestRoute, incidents, forecast])
  const isLiveFlow = flow?.source === 'tomtom'
  const isLiveIncidents = ['tomtom', 'tomtom-flow'].includes(String(incidents?.source || ''))
  const isLiveWeather = ['openweather', 'open-meteo'].includes(String(weather?.source || ''))
  const isLiveRouteDelay = bestRoute?.delay_source === 'tomtom_trafficDelayInSeconds'
  const dashboardIncidentPoints = useMemo(
    () => (incidents?.incidents || []).filter((item) => Number.isFinite(Number(item?.lat)) && Number.isFinite(Number(item?.lon))),
    [incidents],
  )
  const homePeakDelayLiveMinutes = useMemo(() => {
    const values = [
      ...liveDashboardHeatspots
        .map((spot) => Number(spot?.delay_seconds || 0) / 60),
      ...(incidents?.incidents || []).map((item) => Number(item?.delay_seconds || 0) / 60),
    ]
      .filter((value) => Number.isFinite(value) && value > 0)
    if (!values.length) return null
    return Math.max(...values)
  }, [liveDashboardHeatspots, incidents])
  const flowSourceLabel = isLiveFlow ? 'TomTom Live' : flow?.source === 'dataset' ? 'Dataset' : 'Available'
  const incidentSourceLabel = incidents?.source === 'tomtom' ? 'TomTom Live' : incidents?.source === 'tomtom-flow' ? 'TomTom Flow' : incidents?.source === 'dataset' ? 'Dataset' : 'Available'
  const weatherSourceLabel = weather?.source === 'openweather' ? 'OpenWeather Live' : weather?.source === 'open-meteo' ? 'Open-Meteo Live' : 'Available'
  const forecastSourceLabel = forecast?.source === 'model' ? 'Model Forecast' : 'Available'
  const currentTrafficSpeedText = flow?.current_speed_kmph != null
    ? `${Number(flow.current_speed_kmph).toFixed(1)} km/h`
    : '0.0 km/h'
  const peakDelayText = homePeakDelayLiveMinutes != null
    ? `${homePeakDelayLiveMinutes.toFixed(1)} min`
    : delayDisplay !== '--'
      ? delayDisplay
      : '0.0 min'
  const incidentCountText = incidents?.count != null ? `${incidents.count}` : '0'
  const weatherSummaryText = weather?.description || weather?.weather || 'Weather updating'
  const weatherTempText = weather?.temperature_c != null ? `${Number(weather.temperature_c).toFixed(1)} C` : '0.0 C'
  const totalIncidentsText = `${Number(totalIncidents || 0)}`
  const currentAreaShort = currentAreaLabel.replace('Current Area: ', '')
  const airMeta = useMemo(() => aqiMeta(airQuality?.aqi), [airQuality?.aqi])
  const weatherThemeMessage = useMemo(() => {
    const area = String(currentArea || 'Bengaluru').trim() || 'Bengaluru'
    const summary = weather?.description || weather?.weather || 'clear skies'
    if (weatherClass === 'rainy') {
      return `Rain is active near ${area}. The dashboard is now in rain mode with cooler tones and wetter travel context.`
    }
    if (weatherClass === 'storm') {
      return `Storm conditions are active around ${area}. The dashboard switches to a dramatic high-alert atmosphere.`
    }
    if (weatherClass === 'cloudy') {
      return `Cloud cover is building over ${area}. The interface softens into a cooler cloudy mood.`
    }
    if (weatherClass === 'mist') {
      return `Mist or haze is present near ${area}. The dashboard adapts with muted fog-style visuals.`
    }
    return `Skies are ${summary} around ${area}. The interface brightens into a warm sunny mode.`
  }, [currentArea, weather?.description, weather?.weather, weatherClass])
  const homeTrafficState = useMemo(() => {
    const tti = Number(flow?.travel_time_index || 1)
    const speed = Number(flow?.current_speed_kmph || 0)
    const incidentCount = isLiveIncidents ? Number(incidents?.count || 0) : 0
    const ttiNorm = clamp01((tti - 1) / 2)
    const speedNorm = speed > 0 ? clamp01((35 - speed) / 35) : 0
    const incidentNorm = clamp01(incidentCount / 10)
    const score = clamp01(ttiNorm * 0.6 + speedNorm * 0.25 + incidentNorm * 0.15) * 100

    if (score >= 67) return { label: 'Heavy', cls: 'heavy', score, advice: `${isLiveFlow ? 'Live' : 'Current'} traffic shows heavy congestion.` }
    if (score >= 40) return { label: 'Moderate', cls: 'moderate', score, advice: `${isLiveFlow ? 'Live' : 'Current'} traffic shows moderate slowdown.` }
    return { label: 'Light', cls: 'light', score, advice: `${isLiveFlow ? 'Live' : 'Current'} traffic is mostly smooth.` }
  }, [isLiveFlow, isLiveIncidents, flow, incidents])
  const buildAiPayload = useCallback((context, question = '') => ({
    context,
    traffic: {
      status: homeTrafficState?.label || trafficState?.label,
      speed_kmph: flow?.current_speed_kmph,
      travel_time_index: flow?.travel_time_index,
      incidents: incidents?.count,
      hotspots: (focusedTrafficSpots || []).slice(0, 5).map((h) => ({
        label: h.label,
        intensity: h.intensity,
        delay_seconds: h.delay_seconds,
      })),
    },
    weather: {
      summary: weather?.description || weather?.weather,
      temperature_c: weather?.temperature_c,
      humidity: weather?.humidity,
      wind_speed: weather?.wind_speed,
    },
    route: bestRoute
      ? {
          eta: bestRoute.travel_minutes,
          delay: bestRoute.traffic_delay_minutes,
          distance_km: routeDistanceKm(bestRoute),
          score: bestRoute.route_score,
        }
      : {},
    history: { recent: compactHistory(historyItems) },
    urban: {
      poi: effectivePoi || '',
      places: (selectedPlaces || []).slice(0, 5).map((p) => ({ name: p.name, address: p.address })),
    },
    question,
  }), [
    homeTrafficState?.label,
    trafficState?.label,
    flow?.current_speed_kmph,
    flow?.travel_time_index,
    incidents?.count,
    focusedTrafficSpots,
    bestRoute,
    historyItems,
    effectivePoi,
    selectedPlaces,
    weather?.description,
    weather?.weather,
    weather?.temperature_c,
    weather?.humidity,
    weather?.wind_speed,
  ])
  const liveIntensityPct = Math.min(100, Math.max(0, Math.round(homeTrafficState.score || 0)))
  const liveLoadLabel = homeTrafficState.label
  const liveHeatTotal = 1
  useEffect(() => {
    if (!isTrafficMapPage) return
    const hasTrafficData = Boolean(flow || incidents || (liveHeatspots || []).length)
    if (!hasTrafficData) return
    let alive = true
    setTrafficMapSummaryLoading(true)
    setTrafficMapSummaryError(false)
    const payload = buildAiPayload('traffic-map', 'Provide a short traffic summary with bullets: Traffic, Hotspots, Trend, Suggestion.')
    apiPost('/ai-insights', payload)
      .then((res) => {
        if (!alive) return
        const text = res?.insight || ''
        const lines = text
          .split('\n')
          .map((l) => l.replace(/^[-•\s]+/, '').trim())
          .filter(Boolean)
        setTrafficMapSummary(lines.length ? lines : ['Traffic: Moderate', 'Hotspots: MG Road, Silk Board', 'Trend: Increasing next 30 mins', 'Suggestion: Avoid peak zones'])
      })
      .catch((err) => {
        console.error(err)
        if (!alive) return
        setTrafficMapSummaryError(true)
        setTrafficMapSummary(['Unable to load insights. Please refresh.'])
      })
      .finally(() => {
        if (alive) setTrafficMapSummaryLoading(false)
      })
    return () => { alive = false }
  }, [isTrafficMapPage, flow, incidents, liveHeatspots, buildAiPayload])
  const homeLiveOutlook = useMemo(() => {
    const liveCards = liveDashboardHeatspots
      .filter((spot) => {
        const label = String(spot?.label || '')
        return isLocationLike(label)
      })
      .slice(0, 3)
      .map((spot, idx) => {
        const level = trafficLevelFromIntensity(spot.intensity).label
        const liveDelay = Number(spot.delay_seconds || 0)
        return {
          key: `${spot.label || 'spot'}-${idx}`,
          title: shortPlaceName(spot.label, `Corridor ${idx + 1}`),
          level,
          window: liveDelay > 0 ? `${Math.round(liveDelay / 60)} min live delay` : 'Live traffic update',
        }
      })
    const modelCards = (forecast?.cards || []).slice(0, 3).map((c, idx) => ({
      key: `${c.title || c.label || 'forecast'}-${idx}`,
      title: c.title || c.label || `Forecast ${idx + 1}`,
      level: c.level || 'Moderate Traffic',
      window: c.window || c.time || 'Model forecast',
    }))
    const combined = [...liveCards, ...modelCards]
    if (combined.length) return combined.slice(0, 3)
    const area = String(currentArea || 'your area').trim() || 'your area'
    return [
      { key: 'fallback-1', title: area, level: trafficState.label, window: `Current speed ${currentTrafficSpeedText}` },
      { key: 'fallback-2', title: 'Peak Delay', level: trafficState.label, window: peakDelayText },
      { key: 'fallback-3', title: 'Best Available', level: trafficState.label, window: 'Model + live feeds syncing' },
    ]
  }, [liveDashboardHeatspots, forecast?.cards, currentArea, trafficState.label, currentTrafficSpeedText, peakDelayText])
  const routeTrafficStory = useMemo(() => {
    const sourceName = shortPlaceName(routePlan?.source?.label, 'Source')
    const destinationName = shortPlaceName(routePlan?.destination?.label, 'Destination')
    const sourceHotspot = focusedTrafficSpots[0] || null
    const destinationHotspot = focusedTrafficSpots[1] || focusedTrafficSpots[0] || null
    const routeWatch = focusedTrafficSpots.slice(0, 3).map((spot, idx) => ({
      id: `${spot.label || 'spot'}-${idx}`,
      title: shortPlaceName(spot.label, `Traffic zone ${idx + 1}`),
      level: trafficLevelFromIntensity(spot.intensity).label,
      color: trafficLevelFromIntensity(spot.intensity).color,
    }))

    return {
      sourceName,
      destinationName,
      sourceLevel: sourceHotspot ? trafficLevelFromIntensity(sourceHotspot.intensity).label : trafficState.label,
      destinationLevel: destinationHotspot ? trafficLevelFromIntensity(destinationHotspot.intensity).label : trafficState.label,
      routeWatch,
      summary:
        trafficState.label === 'Heavy'
          ? `Traffic is heavy between ${sourceName} and ${destinationName}.`
          : trafficState.label === 'Moderate'
            ? `Traffic is moderate on the way from ${sourceName} to ${destinationName}.`
            : `Traffic is mostly smooth between ${sourceName} and ${destinationName}.`,
    }
  }, [routePlan?.source?.label, routePlan?.destination?.label, focusedTrafficSpots, trafficState.label])

  const trendPoints = useMemo(() => {
    const rows = forecast?.trend || []
    if (!rows.length) return ''
    const width = 620
    const height = 180
    const padX = 30
    const padY = 16
    const maxY = Math.max(...rows.map((r) => Number(r.predicted_volume || 0)), 1)
    const spanX = Math.max(rows.length - 1, 1)
    return rows.map((r, i) => {
      const x = padX + (i / spanX) * (width - padX * 2)
      const y = height - padY - (Number(r.predicted_volume || 0) / maxY) * (height - padY * 2)
      return `${x},${y}`
    }).join(' ')
  }, [forecast])

  const fetchPoi = async (type, lat, lon) => {
    const data = await apiGet(`/nearby-places?lat=${lat}&lon=${lon}&type=${encodeURIComponent(type)}`)
    return data?.places || []
  }

  const runPlan = async (e, nextPage = 'route-plan') => {
    e.preventDefault()
    setLastAction(nextPage)
    setActivePage(nextPage)
    setError('')
    setLoading(true)
    setSourceFocused(false)
    setDestinationFocused(false)
    setSourceSuggestions([])
    setDestinationSuggestions([])

    try {
      const route = await apiPost('/route-plan', {
        source_text: source,
        destination_text: destination,
        depart_in_minutes: Number(departInMinutes),
      })
      setRoutePlan(route)
      setHasRouteResult(true)
      if (nextPage === 'traffic-map') {
        setHasTrafficResult(true)
      }
      setLastUpdated(new Date().toLocaleString())

      const lat = route?.source?.lat
      const lon = route?.source?.lon
      if (lat && lon) {
        const settled = await Promise.allSettled([
          apiGet(`/weather?lat=${lat}&lon=${lon}`),
          apiGet(`/traffic-flow?lat=${lat}&lon=${lon}`),
          apiGet('/incidents'),
          apiGet(`/traffic-forecast?source_area=${encodeURIComponent(source)}`),
        ])
        const [w, f, i, tf] = settled.map((r) => (r.status === 'fulfilled' ? r.value : null))
        const corePoiTypes = ['hospital', 'police', 'fuel']
        const requestedTypes = effectivePoi
          ? (corePoiTypes.includes(effectivePoi) ? corePoiTypes : [...corePoiTypes, effectivePoi])
          : corePoiTypes
        const poiResults = await Promise.all(requestedTypes.map((t) => fetchPoi(t, lat, lon)))
        const poiMap = {}
        requestedTypes.forEach((t, idx) => { poiMap[t] = poiResults[idx] || [] })

        setWeather(w)
        setFlow(f)
        setIncidents(i)
        setPlacesByType((prev) => ({ ...prev, ...poiMap }))
        setForecast(tf || { cards: [], trend: [] })
        if (route?.routes?.[0]) {
          const item = {
            ts: new Date().toLocaleString(),
            source: route?.source?.label || source,
            destination: route?.destination?.label || destination,
            eta: route.routes[0].travel_minutes,
            delay: route.routes[0].traffic_delay_minutes,
            distance: route.routes[0].distance_km,
          }
          if (authToken) {
            await apiPost('/history', {
              source: item.source,
              destination: item.destination,
              eta: item.eta,
              delay: item.delay,
              distance: item.distance,
            }, authToken).catch(() => null)
            apiGet('/history', authToken).then((d) => setHistoryItems(d.items || [])).catch(() => null)
          }
        }
        setActivePage(nextPage)
      }
    } catch (err) {
      setError(err.message || 'Something went wrong')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    const lat = routePlan?.source?.lat
    const lon = routePlan?.source?.lon
    if (!lat || !lon) return
    if (!effectivePoi) return
    if ((placesByType?.[effectivePoi] || []).length > 0) return
    fetchPoi(effectivePoi, lat, lon)
      .then((rows) => setPlacesByType((prev) => ({ ...prev, [effectivePoi]: rows || [] })))
      .catch(() => null)
  }, [effectivePoi, routePlan?.source?.lat, routePlan?.source?.lon])

  const handleLogin = async (e) => {
    e.preventDefault()
    const user = loginUser.trim()
    const pass = loginPass
    const userOk = user.length >= 3
    const passOk = pass.length >= 6
    if (!userOk || !passOk) {
      setLoginError('Username must be at least 3 characters. Password must be at least 6 characters.')
      return
    }
    try {
      const out = await apiPost('/auth/login', { username: user, password: pass })
      localStorage.setItem('route_nova_token', out.token)
      setAuthToken(out.token)
      setIsLoggedIn(true)
      setLoginUser(out.user?.username || user)
      setLoginError('')
      setActivePage('home')
    } catch (err) {
      setLoginError(err.message || 'Incorrect username or password.')
    }
  }

  const handleSignup = async (e) => {
    e.preventDefault()
    const user = loginUser.trim()
    const pass = loginPass
    const userOk = user.length >= 3
    const passOk = pass.length >= 6
    if (!user || !signupEmail.trim() || !pass || !signupPass2.trim()) {
      setLoginError('Please fill all fields.')
      return
    }
    if (!userOk) {
      setLoginError('Username must contain at least 3 characters.')
      return
    }
    if (!passOk) {
      setLoginError('Password must be at least 6 characters.')
      return
    }
    if (pass !== signupPass2) {
      setLoginError('Passwords do not match.')
      return
    }
    try {
      const out = await apiPost('/auth/signup', { username: user, email: signupEmail.trim(), password: pass })
      localStorage.setItem('route_nova_token', out.token)
      setAuthToken(out.token)
      setIsLoggedIn(true)
      setLoginUser(out.user?.username || user)
      setLoginError('')
      setActivePage('home')
    } catch (err) {
      setLoginError(err.message || 'Unable to create account.')
    }
  }

  const startNavigation = () => {
    if (!bestRoute) {
      setNavError('Please compute route first')
      setNavOpen(true)
      return
    }
    setNavError('')
    const rawSteps = bestRoute?.steps || bestRoute?.instructions
    if (Array.isArray(rawSteps) && rawSteps.length) {
      const formatted = rawSteps.map((s) => {
        if (typeof s === 'string') return { text: s }
        return { text: s.text || s.instruction || 'Continue', lat: s.lat, lon: s.lon }
      })
      setNavSteps(formatted)
    } else {
      setNavSteps(generateStepsFromPolyline(bestRoute?.polyline))
    }
    setNavStepIndex(0)
    setNavOpen(true)
    if (navVoice && 'speechSynthesis' in window) {
      const utter = new SpeechSynthesisUtterance((navSteps[0] && navSteps[0].text) || 'Navigation started.')
      window.speechSynthesis.speak(utter)
    }
  }

  const stopNavigation = () => {
    if (navWatchId != null && navigator.geolocation) {
      navigator.geolocation.clearWatch(navWatchId)
    }
    setNavWatchId(null)
    setNavOpen(false)
  }

  const navItems = [
    ['home', 'Home'],
    ['traffic-map', 'Traffic Map'],
    ['route-plan', 'Smart Route'],
    ['weather', 'Weather'],
    ['urban-essentials', 'Urban Essentials'],
    ['history', 'History'],
    ['about', 'About Us'],
  ]
  const handleLogout = () => {
    localStorage.removeItem('route_nova_token')
    setAuthToken('')
    setIsLoggedIn(false)
    setActivePage('login')
  }
  try {
    return (
      <div className={`page weather-theme-${weatherClass}`}>
      {isLoggedIn && <section className="topnav">
        <div className="brand"><span className="brand-pin" aria-hidden="true" /> RouteNova</div>
        <div className="nav-meta">
          {isLoggedIn && navItems.map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={`nav-btn ${activePage === id ? 'active' : ''}`}
              onClick={() => setActivePage(id)}
            >
              {label}
            </button>
          ))}
          <button
            type="button"
            className={`nav-btn ai-nav-btn ${showAiAssistant ? 'active' : ''}`}
            onClick={() => setShowAiAssistant((v) => !v)}
          >
            <svg className="ai-nav-icon" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 4h12a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3H10l-4 3v-3H6a3 3 0 0 1-3-3V7a3 3 0 0 1 3-3z" />
              <circle cx="9" cy="10" r="1.5" />
              <circle cx="12" cy="10" r="1.5" />
              <circle cx="15" cy="10" r="1.5" />
            </svg>
            <span className="ai-tooltip">AI Assistant</span>
          </button>
          <button type="button" className="nav-btn user-pill" onClick={handleLogout}>
            Hi, {loginUser}
          </button>
        </div>
      </section>}

      {(!isLoggedIn && !DEMO_MODE) ? (
        <section className="login-split">
          <div className="login-left">
            <div className="login-brand">
              <span className="project-logo" aria-hidden="true" />
              <span className="project-name">RouteNova</span>
            </div>
            <h1 className="login-title">AI and ML Powered Real-Time Traffic Intelligence and Prediction Platform</h1>
            <p className="login-subtitle">Bengaluru-focused traffic intelligence with real-time routing, live congestion, and predictive insights.</p>
            <ul className="login-points">
              <li>Live congestion heatspots and traffic flow</li>
              <li>Smart route optimization with ETA and delay</li>
              <li>Weather-aware travel recommendations</li>
            </ul>
          </div>
          <div className="panel login-panel">
            <div className="login-head">
              <h2>Welcome to RouteNova</h2>
              <p>Sign in to continue or create a new account to access real-time Bengaluru traffic services.</p>
            </div>
            <form onSubmit={authMode === 'signin' ? handleLogin : handleSignup} className="login-form">
              <p className="auth-form-title">{authMode === 'signin' ? 'Sign In' : 'Create Account'}</p>
              <label htmlFor="login-user">Username</label>
              <input id="login-user" value={loginUser} onChange={(e) => setLoginUser(e.target.value)} placeholder="Enter username" />
              {authMode === 'signup' && (
                <>
                  <label htmlFor="signup-email">Email</label>
                  <input id="signup-email" value={signupEmail} onChange={(e) => setSignupEmail(e.target.value)} placeholder="Enter email" />
                </>
              )}
              <label htmlFor="login-pass">Password</label>
              <div className="password-wrap">
                <input
                  id="login-pass"
                  type={showPassword ? 'text' : 'password'}
                  value={loginPass}
                  onChange={(e) => setLoginPass(e.target.value)}
                  placeholder="Enter password"
                />
                <button type="button" className="eye-btn" onClick={() => setShowPassword((v) => !v)}>
                  {showPassword ? 'Hide' : 'Show'}
                </button>
                </div>
              {authMode === 'signup' && (
                <>
                  <label htmlFor="signup-pass2">Confirm Password</label>
                  <div className="password-wrap">
                    <input
                      id="signup-pass2"
                      type={showConfirmPassword ? 'text' : 'password'}
                      value={signupPass2}
                      onChange={(e) => setSignupPass2(e.target.value)}
                      placeholder="Re-enter password"
                    />
                    <button type="button" className="eye-btn" onClick={() => setShowConfirmPassword((v) => !v)}>
                      {showConfirmPassword ? 'Hide' : 'Show'}
                    </button>
                  </div>
                </>
              )}
              <div className="login-meta">
                {authMode === 'signin' ? (
                  <span>
                    Don&apos;t have an account?{' '}
                    <button type="button" className="link-btn" onClick={() => { setAuthMode('signup'); setLoginError('') }}>
                      Create one
                    </button>
                  </span>
                ) : (
                  <span>
                    Already have an account?{' '}
                    <button type="button" className="link-btn" onClick={() => { setAuthMode('signin'); setLoginError('') }}>
                      Sign in
                    </button>
                  </span>
                )}
                </div>
              <button className="cta login-cta" type="submit">{authMode === 'signin' ? 'Sign In' : 'Create Account'}</button>
              {loginError && <p className="error">{loginError}</p>}
            </form>
          </div>
        </section>
      ) : (
        <section className="hero">
          <div className="hero-overlay" />
          <div className="hero-text">
            <h1><span className="project-logo" aria-hidden="true" /><span className="project-name">RouteNova</span>: AI and ML Powered Real-Time Traffic Intelligence and Prediction Platform</h1>
            <h3>Machine Learning Powered Real-Time Decision Support</h3>
            <div className="hero-weather-pill">
              <span className={`hero-weather-dot ${weatherClass}`} aria-hidden="true" />
              <strong>{weather?.description || weather?.weather || 'Weather sync in progress'}</strong>
              <span>{currentAreaLabel.replace('Current Area: ', '')}</span>
            </div>
            <p className="hero-weather-message">{weatherThemeMessage}</p>
          </div>
        </section>
      )}

      {(isHome || isRoutePlanPage) && (
        <section className="control-card">
          <form onSubmit={runPlan}>
            <div className="grid-top">
              <div className="field" ref={sourceFieldRef}>
                <label>Source</label>
                <input value={source} onChange={(e) => setSource(e.target.value)} onFocus={() => setSourceFocused(true)} placeholder="Indiranagar" />
                {sourceFocused && sourceSuggestions.length > 0 && (
                  <ul className="suggestions">
                    {sourceSuggestions.map((s, idx) => (
                      <li key={`${s.name || s}-${idx}`} onMouseDown={() => { setSource(s.name || ''); setSourceSuggestions([]); setSourceFocused(false) }}>
                        {s.name || s}
                      </li>
                    ))}
                  </ul>
                )}
                </div>

              <div className="field" ref={destinationFieldRef}>
                <label>Destination</label>
                <input value={destination} onChange={(e) => setDestination(e.target.value)} onFocus={() => setDestinationFocused(true)} placeholder="Whitefield" />
                {destinationFocused && destinationSuggestions.length > 0 && (
                  <ul className="suggestions">
                    {destinationSuggestions.map((s, idx) => (
                      <li key={`${s.name || s}-${idx}`} onMouseDown={() => { setDestination(s.name || ''); setDestinationSuggestions([]); setDestinationFocused(false) }}>
                        {s.name || s}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="field compact">
                <label>Depart After (hours)</label>
                <input type="number" min="0" max="24" value={departHours} onChange={(e) => setDepartHours(e.target.value)} />
              </div>

              <div className="field compact">
                <label>Depart After (minutes)</label>
                <input type="number" min="0" max="59" value={departMinutesOnly} onChange={(e) => setDepartMinutesOnly(e.target.value)} />
              </div>

              <div className="field compact">
                <label>POI</label>
                <select value={poiType} onChange={(e) => setPoiType(e.target.value)}>
                  <option value="" disabled>Select POI</option>
                  <option value="none">None</option>
                  <option value="hospital">Hospital</option>
                  <option value="police">Police</option>
                  <option value="fuel">Fuel</option>
                  <option value="hotel">Hotel</option>
                  <option value="restaurant">Restaurant</option>
                  <option value="college">College</option>
                  <option value="university">University</option>
                  <option value="school">School</option>
                  <option value="pharmacy">Pharmacy</option>
                  <option value="atm">ATM</option>
                  <option value="mall">Mall</option>
                  <option value="metro">Metro Station</option>
                </select>
              </div>
              </div>
            <div className="action-row">
              <button className="cta action-btn" disabled={!canSubmit || loading} type="submit">
                {loading && lastAction === 'route-plan'
                  ? 'Computing Real-Time Results...'
                  : hasRouteResult
                    ? 'Route Predicted'
                    : 'Predict Best Route'}
              </button>
              <button type="button" className="cta action-btn" disabled={!canSubmit || loading} onClick={(e) => runPlan(e, 'traffic-map')}>
                {loading && lastAction === 'traffic-map'
                  ? 'Computing Live Traffic...'
                  : hasTrafficResult
                    ? 'Traffic Loaded'
                    : 'Show Live Traffic'}
              </button>
            </div>
            {error && <p className="error">{error}</p>}
          </form>
        </section>
      )}

      {isHome && (
        <section className="ti-dashboard">
          <div className="ti-section-title">
            <h2>Traffic Intelligence Dashboard</h2>
            <p>{currentAreaShort} live overview powered by RouteNova intelligence.</p>
          </div>
          <div className="ti-kpis">
            <div className="ti-kpi kpi-traffic">
              <span>
                Current Traffic
                <em className="data-chip">{flowSourceLabel}</em>
              </span>
              <strong>{homeTrafficState.label}</strong>
              <em>{currentTrafficSpeedText}</em>
            </div>
            <div className="ti-kpi kpi-delay">
              <span>
                Peak Delay
                <em className="data-chip">{homePeakDelayLiveMinutes != null ? 'Live' : isLiveRouteDelay ? 'Route Live' : 'Current'}</em>
              </span>
              <strong>{peakDelayText}</strong>
              <em>{homePeakDelayLiveMinutes != null ? 'From TomTom hotspot delays' : isLiveRouteDelay ? 'From route traffic delay' : 'From current traffic state'}</em>
            </div>
            <div className="ti-kpi kpi-incidents">
              <span>
                Incidents
                <em className="data-chip">{incidentSourceLabel}</em>
              </span>
              <strong>{incidentCountText}</strong>
              <em>{incidents?.source === 'tomtom' ? 'TomTom incident feed' : incidents?.source === 'tomtom-flow' ? 'TomTom live flow points' : 'Dataset-backed road incidents'}</em>
            </div>
            <div className="ti-kpi kpi-weather">
              <span>
                Weather
                <em className="data-chip">{weatherSourceLabel}</em>
              </span>
              <strong>{weatherSummaryText}</strong>
              <em>{weatherTempText}</em>
            </div>
          </div>

          <div className="ti-main">
            <div className="ti-card ti-map">
              <div ref={overviewMapNodeRef} className="ti-map-body" />
            </div>

            <div className="ti-card ti-forecast">
              <div className="ti-card-header">
                <div className="ti-title">
                  <span className="ti-icon forecast" />
                  <h3>Live Traffic Gauge</h3>
                </div>
                <span className="muted">{isLiveFlow ? 'Live feed' : 'Fallback active'}</span>
              </div>
              <div className="live-gauge">
                {liveHeatTotal > 0 ? (
                    <div className="live-gauge-ring" style={{ background: `conic-gradient(#ef4444 0 ${liveIntensityPct}%, rgba(8,31,53,0.6) ${liveIntensityPct}% 100%)` }}>
                      <div className="live-gauge-inner">
                      <strong>{liveLoadLabel}</strong>
                      <span>Live Traffic</span>
                      </div>
                    </div>
                ) : (
                  <div className="ti-forecast-item">Live gauge will appear once the traffic feed responds.</div>
                )}
              </div>
              <div className="sparkline live-only">
                <div className="sparkline-axis">
                  <span>Current Speed</span>
                  <span>{currentTrafficSpeedText}</span>
                </div>
                <div className="sparkline-axis">
                  <span>Free Flow Speed</span>
                  <span>{flow?.free_flow_speed_kmph != null ? `${Number(flow.free_flow_speed_kmph).toFixed(1)} km/h` : '--'}</span>
                </div>
                <div className="sparkline-axis">
                  <span>Incidents</span>
                  <span>{incidentCountText}</span>
                </div>
                <div className="sparkline-labels">
                  <span className="y-label">Live traffic gauge</span>
                  <span className="x-label">{isLiveFlow ? 'Live' : 'Fallback'}</span>
                </div>
              </div>
            </div>
          </div>

          <div className="ti-row">
            <div className="ti-card ti-hotspots-card">
              <div className="ti-card-header">
                <div className="ti-title">
                  <span className="ti-icon hotspots" />
                  <h3>Model-Based Hotspots (Heavy)</h3>
                </div>
                <span className="muted">Dataset</span>
              </div>
              <div className="hotspot-list compact">
                {(() => {
                  // Model-based hotspots should always reflect dataset areas, not current location.
                  const base = (displayHeatspots || []).length
                    ? (displayHeatspots || [])
                    : (liveHeatspots || []).length
                      ? (liveHeatspots || [])
                      : fallbackHeatspots()
                  const modelOnly = base.filter((h) => String(h.source || '') === 'dataset')
                  const heavyModel = modelOnly.filter((h) => Number(h.intensity || 0) >= 0.3)
                  const sourceList = heavyModel.length ? heavyModel : (modelOnly.length ? modelOnly : base)
                  const seen = new Set()
                  const display = []
                  for (const h of sourceList) {
                    const name = String(h.label || '').trim()
                    if (!name) continue
                    const key = name.toLowerCase()
                    if (seen.has(key)) continue
                    seen.add(key)
                    display.push(h)
                    if (display.length >= 7) break
                  }
                  if (display.length < 4) {
                    const filler = [
                      'MG Road',
                      'Indiranagar',
                      'Koramangala',
                      'Whitefield',
                      'HSR Layout',
                      'Jayanagar',
                    ]
                    for (const name of filler) {
                      if (display.length >= 4) break
                      const key = name.toLowerCase()
                      if (seen.has(key)) continue
                      seen.add(key)
                      display.push({ label: name, intensity: 0.35, delay_seconds: null })
                    }
                  }
                  if (!display.length) {
                    return <div className="hotspot-row"><strong>No model hotspots available.</strong></div>
                  }
                  return display.map((h, idx) => (
                    <div key={`${h.label}-${idx}`} className="hotspot-row">
                      <span className="heat-pill pill-high">Model</span>
                      <strong>{h.label || 'Unknown area'}</strong>
                      <span className="muted">{Number(h.delay_seconds || 0) > 0 ? `${Math.round(Number(h.delay_seconds) / 60)} min delay` : 'Model traffic zone'}</span>
                    </div>
                  ))
                })()}
              </div>
            </div>

            <div className="ti-card ti-tip-card">
              <div className="ti-card-header">
                <div className="ti-title">
                  <span className="ti-icon eta" />
                  <h3>Commute Tip of the Moment</h3>
                </div>
                <span className="muted">{currentAreaShort}</span>
              </div>
              <div className="tip-visual">
                <div className="tip-signal" aria-hidden="true">
                  <span className="tip-bar b1" />
                  <span className="tip-bar b2" />
                  <span className="tip-bar b3" />
                </div>
                <div className="tip-body">
                  <strong>{weatherHighlights.travelTip}</strong>
                  <p>{homeTrafficState.advice}</p>
                </div>
              </div>
              <div className="tip-tags">
                <span className="tip-pill">Traffic: {homeTrafficState.label}</span>
                <span className="tip-pill">Weather: {weather?.description || weather?.weather || 'Clear'}</span>
              </div>
            </div>

            <div className="ti-card ti-air-card">
              <div className="ti-card-header">
                <div className="ti-title">
                  <span className="ti-icon air" />
                  <h3>Air Quality</h3>
                </div>
                <span className="muted">Live near {currentAreaShort}</span>
              </div>
              <div className="air-main">
                <div className={`air-score ${airMeta.cls}`}>
                  <span className="air-label">AQI</span>
                  <strong>{airQuality?.aqi ?? '--'}</strong>
                  <em>{airMeta.label}</em>
                </div>
                <div className="air-details">
                  <div className="air-pill">
                    <span>PM2.5</span>
                    <strong>{airQuality?.components?.pm2_5 != null ? `${airQuality.components.pm2_5.toFixed(1)} µg/m³` : '--'}</strong>
                  </div>
                  <div className="air-pill">
                    <span>PM10</span>
                    <strong>{airQuality?.components?.pm10 != null ? `${airQuality.components.pm10.toFixed(1)} µg/m³` : '--'}</strong>
                  </div>
                  <div className="air-pill">
                    <span>NO₂</span>
                    <strong>{airQuality?.components?.no2 != null ? `${airQuality.components.no2.toFixed(1)} µg/m³` : '--'}</strong>
                  </div>
                  <div className="air-pill">
                    <span>O₃</span>
                    <strong>{airQuality?.components?.o3 != null ? `${airQuality.components.o3.toFixed(1)} µg/m³` : '--'}</strong>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>
      )}

      {bestRoute && isRoutePlanPage && (
        <section className="result-strip">
          <div className="result-kpi"><span>Best ETA</span><strong>{bestRoute.travel_minutes} min</strong></div>
          <div className="result-kpi"><span>Traffic Delay</span><strong>{delayDisplay}</strong></div>
          <div className="result-kpi"><span>Distance</span><strong>{routeDistanceKm(bestRoute).toFixed(2)} km</strong></div>
          <div className="result-kpi"><span>Weather</span><strong>{weather?.description || '--'}</strong></div>
          <div className="result-kpi"><span>Incidents</span><strong>{incidents?.count ?? '--'}</strong></div>
          <div className="result-kpi"><span>Updated</span><strong>{lastUpdated || '--'}</strong></div>
          <button type="button" className="cta action-btn" onClick={startNavigation}>
            Start Navigation
          </button>
        </section>
      )}

      {(activePage === 'history') && (
        <section className="panel history-panel">
          <h2>History</h2>
          <ul className="history-list">
            {historyItems.map((h, idx) => (
              <li key={idx}>
                <strong>{h.source} {'->'} {h.destination}</strong>
                <span>{h.ts} | ETA {h.eta} min | Delay {h.delay} min | {h.distance} km</span>
              </li>
            ))}
            {!historyItems.length && <li><span>No history yet. Run route predictions first.</span></li>}
          </ul>
          <div className="ai-history-insight">
            <div className="panel-header">
              <h3>AI Analytics</h3>
              <button
                type="button"
                className="cta action-btn"
                onClick={() => requestAiInsight('history')}
                disabled={aiInsightLoading.history}
              >
                {aiInsightLoading.history ? 'Generating...' : 'Generate Insight'}
              </button>
            </div>
            <div className="ai-insight-body">
              {aiInsights.history || 'Generate AI analytics from your recent routes.'}
            </div>
          </div>
        </section>
      )}

      {(activePage === 'about') && (
        <section className="about-page">
          <div className="about-hero">
            <div className="weather-hero-copy">
              <h2>RouteNova</h2>
              <p>
                An AI and ML powered real-time traffic intelligence platform for Bengaluru that combines predictive analytics,
                live mobility data, and intelligent recommendations for safer and smarter travel decisions.
              </p>
            </div>
            <div className="about-badge">
              <span>Final Year Project</span>
              <strong>Traffic Intelligence Lab</strong>
            </div>
          </div>

          <div className="about-grid">
            <div className="about-card">
              <h3>Who We Are</h3>
              <p>
                We are final year students building RouteNova as an applied AI and ML solution that transforms raw traffic,
                weather, and routing data into practical insights for commuters and urban mobility planning.
              </p>
            </div>
            <div className="about-card">
              <h3>Our Mission</h3>
              <p>
                Deliver an intelligent traffic decision-support platform that is fast, reliable, and easy to understand, so users
                can choose better routes in real time with the help of AI-generated insights and ML-based prediction.
              </p>
            </div>
            <div className="about-card">
              <h3>What We Built</h3>
              <ul>
                <li>Live traffic map with hotspots</li>
                <li>Route optimization with ETA & delay</li>
                <li>Incident severity visualization</li>
                <li>Weather, POI, AI insight, and AI assistant features</li>
              </ul>
            </div>
            <div className="about-card">
              <h3>Technologies Used</h3>
              <ul>
                <li>AI and ML models for prediction, insights, and decision support</li>
                <li>TomTom + Geoapify + OpenWeather APIs</li>
                <li>React UI + FastAPI backend</li>
                <li>Node.js tooling (Vite / npm)</li>
              </ul>
            </div>
            <div className="about-card">
              <h3>Meet the Team</h3>
              <div className="team-grid">
                <div className="team-card">
                  <div className="team-avatar">MT</div>
                  <div>
                    <strong>Mahera Tabassum</strong>
                    <span>Co‑Founder / AI, ML & Frontend</span>
                  </div>
                </div>
                <div className="team-card">
                  <div className="team-avatar">SF</div>
                  <div>
                    <strong>Shaistha Fathima</strong>
                    <span>Co‑Founder / Data & Backend</span>
                  </div>
                </div>
              </div>
            </div>
            <div className="about-card">
              <h3>Future Scope</h3>
              <p>
                RouteNova can be extended with deeper AI capabilities such as advanced forecasting models, conversational trip
                planning, adaptive route recommendation, IoT-based live signal inputs, and smart city dashboards for large-scale
                urban traffic management.
              </p>
            </div>
          </div>
        </section>
      )}

      {(activePage !== 'login' && activePage !== 'history' && activePage !== 'about' && !isHome) && (
        <section className="dashboard-grid">
        {(!isTrafficMapPage && !isRoutePlanPage && !isWeatherPage && !isUrbanPage) && (
          <div className="panel ai-panel">
            <div className="panel-header">
              <h2>AI Insight</h2>
              <button
                type="button"
                className="cta action-btn"
                onClick={() => requestAiInsight(aiContext)}
                disabled={aiInsightLoading[aiContext]}
              >
                {aiInsightLoading[aiContext] ? 'Generating...' : 'Generate Insight'}
              </button>
            </div>
            <div className="ai-insight-body">
              {aiInsights[aiContext] || 'Click generate to see AI recommendations for this page.'}
            </div>
          </div>
        )}
        {isHome && <div className="panel gauge-panel">
          <h2>ML and Traffic Indicators</h2>
          {loading ? (
            <div className="loading-stack"><SkeletonBlock h={92} /><SkeletonBlock h={92} /></div>
          ) : (
            <div className="gauges">
              <GaugeRing label="ETA Pressure" value={eta} max={120} unit="min" color="#0ea5e9" />
              <GaugeRing label="Delay Burden" value={delay} max={60} unit="min" color="#f97316" />
              <GaugeRing label="ML Risk Index" value={riskScore} max={100} unit="idx" color="#e11d48" />
              <GaugeRing label="Travel Time Index" value={flow?.travel_time_index || 0} max={5} unit="tti" color="#059669" />
            </div>
          )}
        </div>}

        {(isRoutePlanPage || isTrafficMapPage) && <div className="panel map-panel">
          <div className="panel-header">
            <h2>{isTrafficMapPage ? 'Bangalore Traffic Level Map' : 'Bangalore Live Map'}</h2>
            {!isTrafficMapPage && (
              <div className="legend">
                <span><i className="dot best" /> Best Route</span>
                {alternativeRoutes.length > 1 && <span><i className="dot alt-1" /> Alternative 1</span>}
                {alternativeRoutes.length > 2 && <span><i className="dot alt-2" /> Alternative 2</span>}
              </div>
            )}
          </div>
          {isTrafficMapPage && (
            <div className={`ai-traffic-summary ${trafficMapSummaryError ? 'error' : ''}`}>
              <div className="ai-traffic-summary-head">
                <strong>AI Traffic Summary</strong>
                <span>{isLiveFlow ? 'Live' : 'Model'} feed</span>
              </div>
              {trafficMapSummaryLoading ? (
                <div className="ai-traffic-loader">
                  <div className="shimmer" />
                  <p>Analyzing live traffic data...</p>
                </div>
              ) : (
                <ul>
                  {(trafficMapSummary || []).map((line, idx) => (
                    <li key={`${line}-${idx}`}>• {line}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <div className="map-wrap">
            <div ref={mapNodeRef} className="map" />
          </div>
        </div>}

        {isHome && <div className="panel live-panel">
          <h2>Live City Feed</h2>
          <div className="live-row"><span>Weather</span><strong>{weather ? `${weather.description}` : '--'}</strong></div>
          <div className="live-row"><span>Temperature</span><strong>{weather?.temperature_c ?? '--'} C</strong></div>
          <div className="live-row"><span>Flow Speed</span><strong>{flow?.current_speed_kmph ?? '--'} km/h</strong></div>
          <div className="live-row"><span>Road Closure</span><strong>{flow?.road_closure ? 'Yes' : 'No'}</strong></div>
          <div className="live-row"><span>Incidents</span><strong>{incidents?.count ?? '--'}</strong></div>
        </div>}

        {isWeatherPage && <div className="panel weather-panel">
          <div className="panel-header">
            <h2>Weather Studio</h2>
            <span className="weather-badge">{weather?.weather || 'No Data'}</span>
          </div>
          <div className={`weather-hero ${weatherClass}`}>
            <div className="weather-hero-main">
              <div className={`weather-anim ${weatherClass}`} aria-hidden="true">
                <span className="sun-core" />
                <span className="sun-ray r1" />
                <span className="sun-ray r2" />
                <span className="cloud c1" />
                <span className="cloud c2" />
                <span className="rain-drop d1" />
                <span className="rain-drop d2" />
              </div>
              <div className="weather-hero-copy">
                <span className="weather-eyebrow">Live weather in Bengaluru</span>
                <h3>{weather?.description || 'Weather Update'}</h3>
                <div className="weather-temp-line">
                  <strong>{weather?.temperature_c ?? '--'} C</strong>
                  <span>Feels like {weatherHighlights.feels} C</span>
                </div>
                <p>{weatherHighlights.travelTip}</p>
              </div>
            </div>
            <div className="weather-hero-side">
              <div className="weather-hero-stat">
                <span>Wind</span>
                <strong>{weatherHighlights.windLevel}</strong>
              </div>
              <div className="weather-hero-stat">
                <span>Humidity</span>
                <strong>{weather?.humidity ?? '--'}%</strong>
              </div>
              <div className="weather-hero-stat">
                <span>Comfort</span>
                <strong>{weatherHighlights.comfort}</strong>
              </div>
            </div>
            <div className="weather-pill">{weatherHighlights.rainRisk} rain risk</div>
          </div>
          <div className="weather-spotlight">
            <div className="weather-spotlight-card current">
              <span>Now</span>
              <strong>{weather?.temperature_c ?? '--'} C</strong>
              <em>{weather?.description || '--'}</em>
            </div>
            <div className="weather-spotlight-card next">
              <span>Outdoor Feel</span>
              <strong>{weatherHighlights.feels} C</strong>
              <em>{weatherHighlights.windLevel} with {weatherHighlights.rainRisk} rain risk</em>
            </div>
            <div className="weather-spotlight-card tomorrow">
              <span>Tomorrow</span>
              <strong>{Number(weather?.temperature_c || 0) ? (Number(weather?.temperature_c || 0) + 2).toFixed(1) : '--'} C</strong>
              <em>{weather?.description || '--'}</em>
            </div>
          </div>
          <div className="weather-grid">
            <div className="weather-card">
              <span>Temperature</span>
              <strong>{weather?.temperature_c ?? '--'} C</strong>
            </div>
            <div className="weather-card">
              <span>Feels Like</span>
              <strong>{weather?.feels_like_c ?? '--'} C</strong>
            </div>
            <div className="weather-card">
              <span>Wind Speed</span>
              <strong>{weather?.wind_speed ?? '--'} m/s</strong>
            </div>
            <div className="weather-card">
              <span>Condition</span>
              <strong>{weather?.description || '--'}</strong>
            </div>
          </div>
          <div className="humidity-block">
            <div className="humidity-head">
              <div>
                <span>Humidity Balance</span>
                <strong>{weather?.humidity ?? '--'}%</strong>
              </div>
              <em>{weatherHighlights.rainRisk} rain risk</em>
            </div>
            <div className="humidity-track">
              <div
                className="humidity-fill"
                style={{ width: `${Math.max(0, Math.min(100, Number(weather?.humidity || 0)))}%` }}
              />
            </div>
          </div>
          <div className="weather-highlights">
            <div className="highlight-card">
              <span>Comfort Index</span>
              <strong>{weatherHighlights.comfort}</strong>
            </div>
            <div className="highlight-card">
              <span>Dew Point</span>
              <strong>{weatherHighlights.dew} C</strong>
            </div>
            <div className="highlight-card">
              <span>Wind Level</span>
              <strong>{weatherHighlights.windLevel}</strong>
            </div>
            <div className="highlight-card">
              <span>Rain Risk</span>
              <strong>{weatherHighlights.rainRisk}</strong>
            </div>
          </div>
          <div className="weather-visuals">
            <div className="visual-card">
              <div className="visual-header">
                <span className="visual-dot temp" />
                <strong>Temperature Pulse</strong>
              </div>
              <div className="gauge-visual">
                <div
                  className="ring"
                  style={{ background: `conic-gradient(#60a5fa ${pct01((Number(weather?.temperature_c || 0) + 5) * 2)}%, #0b2440 0)` }}
                >
                  <div className="ring-center">
                    <strong>{weather?.temperature_c ?? '--'} C</strong>
                    <span>Now</span>
                  </div>
                </div>
                <div className="ring-meta">
                  <div><span>Feels Like</span><strong>{weatherHighlights.feels} C</strong></div>
                  <div><span>Dew Point</span><strong>{weatherHighlights.dew} C</strong></div>
                </div>
              </div>
            </div>
            <div className="visual-card">
              <div className="visual-header">
                <span className="visual-dot humid" />
                <strong>Air Moisture</strong>
              </div>
              <div className="bar-visual">
                <div className="bar-track">
                  <div className="bar-fill" style={{ width: `${pct01(weather?.humidity)}%` }} />
                </div>
                <div className="bar-meta">
                  <span>{weather?.humidity ?? '--'}%</span>
                  <span>{weatherHighlights.rainRisk} rain risk</span>
                </div>
              </div>
            </div>
            <div className="visual-card weather-story-card">
              <div className="visual-header">
                <span className="visual-dot wind" />
                <strong>Outdoor Mood</strong>
              </div>
              <div className="weather-story">
                <div className="story-chip">
                  <span>Comfort</span>
                  <strong>{weatherHighlights.comfort}</strong>
                </div>
                <div className="story-chip">
                  <span>Wind</span>
                  <strong>{weatherHighlights.windLevel}</strong>
                </div>
                <div className="story-chip">
                  <span>Dew Point</span>
                  <strong>{weatherHighlights.dew} C</strong>
                </div>
              </div>
            </div>
          </div>
          <div className="weather-advisory">
            <strong>Travel Advisory</strong>
            <p>{weatherHighlights.travelTip}</p>
          </div>
        </div>}

        {isRoutePlanPage && <div className="panel traffic-level-panel">
          <div className="panel-header">
            <h2>Traffic Level</h2>
            <span className={`traffic-level-chip ${trafficState.cls}`}>{trafficState.label}</span>
          </div>
          <div className="traffic-level-grid">
            <div className="traffic-congestion">
              <p>Traffic Congestion</p>
              <div className="level-item"><i className="dot-level level-light" /> Light Traffic</div>
              <div className="level-item"><i className="dot-level level-moderate" /> Moderate Traffic</div>
              <div className="level-item"><i className="dot-level level-heavy" /> Heavy Traffic</div>
            </div>
            <div className="traffic-meter-wrap">
              <div className="traffic-meter">
                <div className="traffic-meter-arc" />
                <div className="traffic-meter-mask" />
                <div className="traffic-meter-needle" style={{ transform: `translateX(-50%) rotate(${-90 + (trafficState.score / 100) * 180}deg)` }} />
                <div className="traffic-meter-center" />
              </div>
              <div className="traffic-meter-labels">
                <strong>{trafficState.label}</strong>
                <span>{trafficState.score.toFixed(0)}/100</span>
              </div>
            </div>
          </div>
          <div className="traffic-notes">
            <div className="live-row"><span>Expected Delay</span><strong>{delayDisplay}</strong></div>
            <div className="live-row"><span>Current Speed</span><strong>{flow?.current_speed_kmph ?? '--'} km/h</strong></div>
            <div className="live-row"><span>Recommendation</span><strong>{trafficState.advice}</strong></div>
          </div>
        </div>}

        {isRoutePlanPage && <div className="panel insights-panel">
          <div className="panel-header gradient-header">
            <h2>Smart Route Insights</h2>
            <span className="muted">Decision support</span>
          </div>
          <div className="insight-grid">
            <div className="insight-item">
              <div className="insight-icon clock" />
              <div className="insight-copy">
                <span>Time Saved</span>
                <strong>{routeInsights.timeSaved.toFixed(1)} min</strong>
              </div>
            </div>
            <div className="insight-item">
              <div className="insight-icon route" />
              <div className="insight-copy">
                <span>Fastest Route</span>
                <strong>{routeInsights.fastest}</strong>
              </div>
            </div>
            <div className="insight-item">
              <div className="insight-icon alert" />
              <div className="insight-copy">
                <span>Traffic Status</span>
                <strong>{trafficState.label}</strong>
              </div>
            </div>
          </div>
          <div className="insight-meta">
            <div className="insight-meta-row"><span>Slowest Route</span><strong>{routeInsights.slowest}</strong></div>
            <div className="insight-meta-row"><span>Recommendation</span><strong>{routeInsights.recommendation}</strong></div>
          </div>
          <div className="route-bars">
            {alternativeRoutes.map((r, idx) => {
              const times = alternativeRoutes.map((it) => Number(it.travel_minutes || 0))
              const maxTime = Math.max(...times, 1)
              const pct = Math.max(8, (Number(r.travel_minutes || 0) / maxTime) * 100)
              return (
                <div key={idx} className="route-bar-row">
                  <span>{routeNames[idx]}</span>
                  <div className="route-bar-track">
                    <div className="route-bar-fill" style={{ width: `${pct}%`, background: routeColors[idx] }} />
                  </div>
                  <em>{Number(r.travel_minutes || 0).toFixed(1)} min</em>
                </div>
              )
            })}
          </div>
        </div>}

        {isRoutePlanPage && <div className="panel traffic-insights-panel">
          <div className="panel-header gradient-header">
            <h2>Traffic Insights</h2>
            <span className="muted">Live road conditions</span>
          </div>
          <div className="traffic-insights-grid">
            <div className="traffic-insight-card">
              <span>Average Speed</span>
              <strong>{trafficInsights.avgSpeed ? `${trafficInsights.avgSpeed.toFixed(1)} km/h` : '--'}</strong>
            </div>
            <div className="traffic-insight-card">
              <span>Congestion Level</span>
              <strong>{trafficInsights.congestion}</strong>
            </div>
            <div className="traffic-insight-card">
              <span>Total Delay</span>
              <strong>{trafficInsights.delay} min</strong>
            </div>
            <div className="traffic-insight-card">
              <span>Most Congested Area</span>
              <strong>{trafficInsights.worst}</strong>
            </div>
          </div>
          <div className="traffic-insights-extras">
            <div className="traffic-kpi">
              <span>Segments Tracked</span>
              <strong>{trafficSegments.length}</strong>
            </div>
            <div className="traffic-kpi">
              <span>Typical Delay Band</span>
              <strong>{trafficInsights.congestion === 'High' ? '10-15 min' : trafficInsights.congestion === 'Medium' ? '5-9 min' : '0-4 min'}</strong>
            </div>
            <div className="traffic-kpi">
              <span>Flow Coverage</span>
              <strong>{Math.min(100, Math.round((trafficSegments.length / 18) * 100))}%</strong>
            </div>
          </div>
          <div className="traffic-dominant">
            <span>Current Traffic: {dominantTraffic.label}</span>
            <em>{dominantTraffic.desc}</em>
          </div>
          <div className="traffic-bars">
            <div className="traffic-bars-head">
              <span>Traffic Levels</span>
              <span className="traffic-info" title="Traffic levels are based on real-time congestion analysis.">i</span>
            </div>
            <div className="traffic-bar-row">
              <span>Light</span>
              <div className="traffic-bar-track">
                <div className="traffic-bar-fill smooth" style={{ width: `${trafficPct.smooth}%` }} />
              </div>
              <em>({trafficPct.smooth}%) Smooth traffic</em>
            </div>
            <div className="traffic-bar-row">
              <span>Moderate</span>
              <div className="traffic-bar-track">
                <div className="traffic-bar-fill moderate" style={{ width: `${trafficPct.moderate}%` }} />
              </div>
              <em>({trafficPct.moderate}%) Slow moving</em>
            </div>
            <div className="traffic-bar-row">
              <span>Heavy</span>
              <div className="traffic-bar-track">
                <div className="traffic-bar-fill heavy" style={{ width: `${trafficPct.heavy}%` }} />
              </div>
              <em>({trafficPct.heavy}%) Congested</em>
            </div>
          </div>
          <div className="traffic-note">
            Recommendation: {trafficInsights.congestion === 'High' ? 'Avoid peak corridors and prefer alternates.' : 'Roads are mostly clear.'}
          </div>
        </div>}

        {isRoutePlanPage && <div className="panel routes-panel">
          <div className="panel-header gradient-header">
            <h2>Route Comparison</h2>
            <span className="muted">Best + Alternatives</span>
          </div>
          <div className="route-table-wrap">
            <table className="route-table">
              <thead>
                <tr>
                  <th>Route Name</th>
                  <th>Traffic Level</th>
                  <th>Distance</th>
                  <th>Estimated Time</th>
                  <th>Delay</th>
                </tr>
              </thead>
              <tbody>
                {alternativeRoutes.map((r, idx) => {
                  const level = routeTrafficLevel(r.traffic_delay_minutes)
                  const isBest = idx === 0
                  return (
                    <tr
                      key={idx}
                      className={`${isBest ? 'top' : ''} ${selectedRouteIdx === idx ? 'selected' : ''}`}
                      onClick={() => setSelectedRouteIdx(idx)}
                      role="button"
                    >
                      <td>
                        <span className="route-dot" style={{ background: routeColors[idx % routeColors.length] }} />
                        <strong>{routeNames[idx] || `Alt ${idx}`}</strong>
                        {isBest && <RouteBadge />}
                      </td>
                      <td><LevelBadge level={level} /></td>
                      <td>{routeDistanceKm(r).toFixed(2)} km</td>
                      <td>{Number(r.travel_minutes || 0).toFixed(1)} min</td>
                      <td>{Number(r.traffic_delay_minutes || 0).toFixed(1)} min</td>
                    </tr>
                  )
                })}
                {!alternativeRoutes.length && (
                  <tr>
                    <td colSpan={5} className="muted">Run prediction to view route options.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {alternativeRoutes.length < 2 && (
            <p className="muted" style={{ marginTop: 12 }}>
              Only one real route was returned. Try a different source/destination or time.
              {routePlan?.alternatives_meta && (
                <span>
                  {' '}TomTom: {routePlan.alternatives_meta.tomtom_primary}, ORS added: {routePlan.alternatives_meta.ors_added}.
                </span>
              )}
            </p>
          )}
        </div>}

        {isRoutePlanPage && <div className="panel snapshot-panel">
          <div className="panel-header gradient-header">
            <h2>Live Mobility Snapshot</h2>
            <span className="muted">Key city indicators</span>
          </div>
          <div className="snapshot-grid">
            <div className="snapshot-card">
              <div className="snapshot-icon speed" />
              <div>
                <span>Live Speed Index</span>
                <strong>{flow?.current_speed_kmph ? `${Number(flow.current_speed_kmph).toFixed(1)} km/h` : '--'}</strong>
              </div>
            </div>
            <div className="snapshot-card">
              <div className="snapshot-icon risk" />
              <div>
                <span>Congestion Risk</span>
                <strong>{trafficState.label}</strong>
              </div>
            </div>
            <div className="snapshot-card">
              <div className="snapshot-icon incidents" />
              <div>
                <span>Incident Count</span>
                <strong>{incidents?.count ?? '--'}</strong>
              </div>
            </div>
          </div>
        </div>}


        {isTrafficMapPage && <div className="panel hotspots-panel">
          <div className="ti-title">
            <span className="ti-icon hotspots" />
            <h2>Traffic For Your Trip</h2>
            <span className="refresh-badge">
              Updated {lastTrafficUpdate ? new Date(lastTrafficUpdate).toLocaleTimeString() : '--'}
            </span>
          </div>
          <div className="traffic-conditions">
            <div className="traffic-route-summary">
              <div className="traffic-summary-head">
                <strong>{routeTrafficStory.summary}</strong>
                <span>{delayDisplay}</span>
              </div>
              <div className="traffic-trip-grid">
                <div className="traffic-trip-card">
                  <span>Source</span>
                  <strong>{routeTrafficStory.sourceName}</strong>
                  <em>{routeTrafficStory.sourceLevel} traffic</em>
                </div>
                <div className="traffic-trip-card">
                  <span>Destination</span>
                  <strong>{routeTrafficStory.destinationName}</strong>
                  <em>{routeTrafficStory.destinationLevel} traffic</em>
                </div>
                <div className="traffic-trip-card">
                  <span>Current speed</span>
                  <strong>{trafficInsights.avgSpeed ? `${trafficInsights.avgSpeed.toFixed(1)} km/h` : '--'}</strong>
                  <em>{trafficInsights.congestion} congestion</em>
                </div>
              </div>
            </div>

          <div className="traffic-bars">
            <div className="traffic-bars-head">
              <span>Traffic Levels</span>
              <span className="traffic-info" title="Traffic levels are based on real-time congestion analysis.">i</span>
            </div>
            <div className="traffic-bar-row">
              <span>Light</span>
              <div className="traffic-bar-track">
                <div className="traffic-bar-fill smooth" style={{ width: `${trafficPct.smooth}%` }} />
              </div>
              <em>({trafficPct.smooth}%) Smooth traffic</em>
            </div>
            <div className="traffic-bar-row">
              <span>Moderate</span>
              <div className="traffic-bar-track">
                <div className="traffic-bar-fill moderate" style={{ width: `${trafficPct.moderate}%` }} />
              </div>
              <em>({trafficPct.moderate}%) Slow moving</em>
            </div>
            <div className="traffic-bar-row">
              <span>Heavy</span>
              <div className="traffic-bar-track">
                <div className="traffic-bar-fill heavy" style={{ width: `${trafficPct.heavy}%` }} />
              </div>
              <em>({trafficPct.heavy}%) Congested</em>
            </div>
            <div className="traffic-bar-row">
              <span>Severe</span>
              <div className="traffic-bar-track">
                <div className="traffic-bar-fill severe" style={{ width: `${trafficPct.severe}%` }} />
              </div>
              <em>({trafficPct.severe}%) Highly congested</em>
            </div>
          </div>
            <div className="traffic-note">
              Recommendation: {trafficState.advice}
            </div>
          </div>
        </div>}

        {isUrbanPage && (
          <div className={`urban-wrap poi-${poiVisualKey(effectivePoi)}`}>
            <div className="panel places-panel">
          <h2>Urban Essentials: {effectivePoi ? `Nearby ${effectivePoi}` : 'Select a POI'}</h2>
          <p className="poi-message">{poiSuggestion(effectivePoi)}</p>
          {!effectivePoi && (
            <div className="poi-empty">
              <strong>No POI selected</strong>
              <span>Choose a POI to view nearby essentials and services around your current area.</span>
            </div>
          )}
          {effectivePoi && (
            <ul>
              {selectedPlaces.slice(0, 6).map((p, idx) => (
                <li key={idx}><strong>{p.name || 'Unknown'}</strong><span>{p.address || 'No address'}</span></li>
              ))}
              {!selectedPlaces.length && <li><span>No places loaded yet. Run prediction first.</span></li>}
            </ul>
          )}
            </div>
          </div>
        )}

      </section>
      )}
      {showAiAssistant && (
        <div className="ai-float">
          <div className="ai-float-header">
            <div>
              <strong>RouteNova AI Assistant</strong>
              <span>Smart Traffic Guidance</span>
            </div>
            <div className="ai-header-actions">
              <button type="button" className={`ai-voice ${voiceEnabled ? 'active' : ''}`} onClick={() => setVoiceEnabled((v) => !v)}>
                {voiceEnabled ? 'Voice On' : 'Voice Off'}
              </button>
              <button type="button" className="ai-clear" onClick={clearAiChat}>Clear</button>
              <button type="button" className="ai-close" onClick={() => setShowAiAssistant(false)}>×</button>
            </div>
          </div>
          <div className="ai-float-quick">
            <button type="button" onClick={handleQuickBestRoute}>Best route now</button>
            <button type="button" onClick={handleQuickTrafficNear}>Traffic near me</button>
            <button type="button" onClick={handleQuickBestTime}>Best travel time today</button>
            <button type="button" onClick={handleQuickWeatherImpact}>Weather impact</button>
          </div>
          <div className="ai-float-body">
            {aiChatMessages.map((m, idx) => (
              <div key={`${m.role}-${idx}`} className={`ai-bubble ${m.role}`}>
                <div className="ai-bubble-text">{m.text}</div>
                {m.ts && <div className="ai-bubble-time">{new Date(m.ts).toLocaleTimeString()}</div>}
              </div>
            ))}
            {aiChatLoading && <div className="ai-bubble assistant">Analyzing traffic data...</div>}
            <div ref={aiChatEndRef} />
          </div>
          <div className="ai-float-input">
            <input
              value={aiChatInput}
              onChange={(e) => setAiChatInput(e.target.value)}
              placeholder="Ask RouteNova AI..."
              onKeyDown={(e) => { if (e.key === 'Enter') sendAiChat() }}
            />
            <button type="button" className={`ai-mic ${isListening ? 'listening' : ''}`} onClick={startListening} title="Voice input">
              🎤
            </button>
            <button type="button" onClick={sendAiChat} disabled={aiChatLoading}>Send</button>
          </div>
        </div>
      )}
      {navOpen && (
        <div className="nav-float">
          <div className="nav-float-header">
            <div>
              <strong>Live Navigation</strong>
              <span>{bestRoute ? `${bestRoute.travel_minutes} min ETA` : 'Route required'}</span>
            </div>
            <div className="nav-actions">
              <button type="button" className={`nav-voice ${navVoice ? 'active' : ''}`} onClick={() => setNavVoice((v) => !v)}>
                {navVoice ? 'Voice On' : 'Voice Off'}
              </button>
              <button type="button" className="nav-stop" onClick={stopNavigation}>Stop Navigation</button>
            </div>
          </div>
          <div className="nav-steps">
            {navError && <div className="nav-error">{navError}</div>}
            {!navError && navSteps.map((step, idx) => (
              <div key={`${step.text}-${idx}`} className={`nav-step ${idx === navStepIndex ? 'active' : ''}`}>
                <span>{idx + 1}.</span>
                <strong>{step.text}</strong>
              </div>
            ))}
            {!navError && !navSteps.length && <div className="nav-error">Please compute route first</div>}
          </div>
        </div>
      )}
      </div>
    )
  } catch (err) {
    console.error('UI render error', err)
    return (
      <div className="page">
        <section className="panel login-panel">
          <h2>RouteNova</h2>
          <p className="muted">The dashboard failed to render. Please refresh the page.</p>
        </section>
      </div>
    )
  }
}

