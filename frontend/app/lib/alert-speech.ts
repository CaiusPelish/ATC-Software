// Turns a backend conflict into what the controller reads and hears.
import { placeName } from "~/lib/chart";
import type { CollisionWarning, Conflict, SimulationState } from "~/lib/sim-state";

export type ConflictAlert = {
  /** Same pair + place = same alert, so it's announced once, not every poll. */
  key: string;
  /** The two parties, order-free: a predicted conflict and the real one share it. */
  pair: string;
  kind: "runway" | "separation" | "predicted";
  severity: Conflict["severity"];
  title: string;
  text: string;
  spoken: string;
  ids: string[];
  /** Node id where it happens (or the runway pair). */
  location: string;
  /** Predicted only: seconds until they meet. */
  eta?: number;
};

const pairOf = (ids: string[]) => [...ids].sort().join("+");

const AIRLINES: Record<string, string> = {
  AAL: "American", BAW: "Speedbird", DAL: "Delta", JBU: "JetBlue", RPA: "Brickyard",
  SWA: "Southwest", UAL: "United", ASA: "Alaska", FFT: "Frontier", NKS: "Spirit",
  FDX: "FedEx", UPS: "UPS", ACA: "Air Canada", DLH: "Lufthansa", AFR: "Air France",
  ENY: "Envoy", EDV: "Endeavor", JIA: "Blue Streak", SKW: "SkyWest", EJA: "Execjet",
};
const DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "niner"];
const NATO: Record<string, string> = {
  A: "Alpha", B: "Bravo", C: "Charlie", D: "Delta", E: "Echo", F: "Foxtrot", G: "Golf", H: "Hotel",
  I: "India", J: "Juliett", K: "Kilo", L: "Lima", M: "Mike", N: "November", O: "Oscar", P: "Papa",
  Q: "Quebec", R: "Romeo", S: "Sierra", T: "Tango", U: "Uniform", V: "Victor", W: "Whiskey",
  X: "X-ray", Y: "Yankee", Z: "Zulu",
};

const spellDigits = (s: string) => [...s].map((c) => (/\d/.test(c) ? DIGITS[+c] : c)).join(" ");

/** "UAL1510" -> "United one five one zero", "TRUCK01" -> "Truck zero one". */
export function speakCallsign(label: string) {
  const m = label.match(/^([A-Z]+)-?(\d+)$/i);
  if (!m) return label;
  const [, letters, digits] = m;
  const name = AIRLINES[letters.toUpperCase()] ?? (letters.length > 3 ? letters[0] + letters.slice(1).toLowerCase() : [...letters].join(" "));
  return `${name} ${spellDigits(digits)}`;
}

/** One taxiway name: "B" -> "Bravo", "RD" -> "Romeo Delta", "P2" -> "Papa two". */
const speakTaxiway = (name: string) =>
  [...name.replace(/(\D)(\d)/, "$1 $2")]
    .map((c) => (c === " " ? "" : (NATO[c] ?? (/\d/.test(c) ? DIGITS[+c] : c))))
    .filter(Boolean)
    .join(" ");

/** "taxiway A/RD" -> "taxiway Alpha and Romeo Delta", "runway 4R/22L" -> "runway four right, two two left". */
function speakPlace(place: string) {
  const runwayEnd = (r: string) => {
    const m = r.match(/^(\d+)([LRC]?)$/);
    return m ? [spellDigits(m[1]), { L: "left", R: "right", C: "center" }[m[2]] ?? ""].filter(Boolean).join(" ") : r;
  };
  if (place.startsWith("taxiway ")) return `taxiway ${place.slice(8).split("/").map(speakTaxiway).join(" and ")}`;
  if (place.startsWith("runway ")) return `runway ${place.slice(7).split("/").map(runwayEnd).join(", ")}`;
  return place;
}

/** Only letters, digits and simple punctuation reach the voice service. */
const speakable = (s: string) => s.replace(/[^A-Za-z0-9 ,.'-]/g, " ").replace(/\s+/g, " ").trim();

export function describeConflict(conflict: Conflict, state: SimulationState): ConflictAlert {
  const label = (id: string) =>
    state.aircraft.find((a) => a.id === id)?.callsign ??
    (state.vehicles.some((v) => v.id === id) ? id : "landing traffic");
  const names = conflict.aircraft_ids.map(label);
  const [a, b = "traffic"] = names;
  const pair = pairOf(conflict.aircraft_ids);
  const key = `${pair}@${conflict.location}`;
  const base = { key, pair, severity: conflict.severity, ids: conflict.aircraft_ids, location: conflict.location };

  if (conflict.location.includes("/")) {
    // Runway pair like "4R/22L": the arrival lands on the second half in the sim's south-west flow.
    const runway = `runway ${conflict.location}`;
    return {
      ...base,
      kind: "runway",
      title: "Runway incursion",
      text: `${a} is on ${runway} with ${b} using it.`,
      spoken: speakable(`Runway incursion. ${speakCallsign(a)}, hold short. ${speakPlace(runway)}.`),
    };
  }

  const place = placeName(conflict.location);
  const close = conflict.severity === "critical" ? "less than 20 metres" : "less than 40 metres";
  return {
    ...base,
    kind: "separation",
    title: "Conflict in path",
    text: `${a} and ${b} are ${close} apart on ${place}.`,
    spoken: speakable(`Conflict in path. ${speakCallsign(a)} and ${speakCallsign(b)}, ${speakPlace(place)}. Hold position.`),
  };
}

/** A collision the backend predicts a few seconds ahead: said before it happens. */
export function describeWarning(warning: CollisionWarning, state: SimulationState): ConflictAlert {
  const label = (id: string) =>
    state.aircraft.find((a) => a.id === id)?.callsign ??
    (state.vehicles.some((v) => v.id === id) ? id : "traffic");
  const [a, b = "traffic"] = warning.aircraft_ids.map(label);
  const pair = pairOf(warning.aircraft_ids);
  const place = placeName(warning.location);
  const seconds = Math.max(1, Math.round(warning.eta));
  return {
    key: `pred:${pair}@${warning.location}`,
    pair,
    kind: "predicted",
    severity: "warning",
    ids: warning.aircraft_ids,
    location: warning.location,
    eta: warning.eta,
    title: "Conflict in path",
    text: `${a} will meet ${b} on ${place} in ${seconds} s.`,
    spoken: speakable(
      `Traffic alert. ${speakCallsign(a)}, conflict with ${speakCallsign(b)}, ${speakPlace(place)}, in ${seconds} seconds. Hold position.`,
    ),
  };
}
