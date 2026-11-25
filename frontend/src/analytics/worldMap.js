// The map of the "Map" visual: country borders (world-atlas, Natural Earth, public domain) and where well-known places are.
// world-atlas is TopoJSON: shared border lines ("arcs", delta-encoded) that are put together into polygons here.
import * as echarts from 'echarts/core';

export const normal = (text) => String(text ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();

const ALIASES = {
  usa: 'United States of America', us: 'United States of America', 'united states': 'United States of America', america: 'United States of America',
  uk: 'United Kingdom', 'great britain': 'United Kingdom', england: 'United Kingdom', uae: 'United Arab Emirates', ksa: 'Saudi Arabia',
  'czech republic': 'Czechia', 'south korea': 'South Korea', 'republic of korea': 'South Korea', 'korea south': 'South Korea', 'north korea': 'North Korea',
  'russian federation': 'Russia', 'viet nam': 'Vietnam', turkiye: 'Turkey', burma: 'Myanmar', 'ivory coast': "Côte d'Ivoire", 'cote d ivoire': "Côte d'Ivoire",
  'bosnia and herzegovina': 'Bosnia and Herz.', bosnia: 'Bosnia and Herz.', 'dominican republic': 'Dominican Rep.', 'central african republic': 'Central African Rep.',
  'democratic republic of the congo': 'Dem. Rep. Congo', 'dr congo': 'Dem. Rep. Congo', drc: 'Dem. Rep. Congo', 'congo kinshasa': 'Dem. Rep. Congo', 'congo brazzaville': 'Congo', 'republic of the congo': 'Congo',
  'equatorial guinea': 'Eq. Guinea', 'south sudan': 'S. Sudan', swaziland: 'eSwatini', eswatini: 'eSwatini', 'western sahara': 'W. Sahara', 'solomon islands': 'Solomon Is.',
  macedonia: 'Macedonia', 'north macedonia': 'Macedonia', holland: 'Netherlands', 'the netherlands': 'Netherlands', iran: 'Iran', 'islamic republic of iran': 'Iran', 'syrian arab republic': 'Syria', 'lao pdr': 'Laos', 'brunei darussalam': 'Brunei', 'east timor': 'Timor-Leste',
};

/** [longitude, latitude] of places people often have by name only. */
const PLACES = {
  karachi: [67.01, 24.86], lahore: [74.34, 31.55], faisalabad: [73.08, 31.42], rawalpindi: [73.05, 33.6], islamabad: [73.05, 33.68], multan: [71.47, 30.2],
  peshawar: [71.52, 34.01], quetta: [67.0, 30.18], hyderabad: [68.37, 25.39], sialkot: [74.53, 32.49], gujranwala: [74.19, 32.16], bahawalpur: [71.68, 29.4],
  sargodha: [72.67, 32.08], sukkur: [68.86, 27.71], larkana: [68.21, 27.56], abbottabad: [73.22, 34.15], mardan: [72.04, 34.2], gujrat: [74.08, 32.57], sahiwal: [73.11, 30.67], 'rahim yar khan': [70.3, 28.42],
  'dera ghazi khan': [70.63, 30.05], gwadar: [62.33, 25.12], muzaffarabad: [73.47, 34.37], gilgit: [74.31, 35.92], mirpur: [73.75, 33.15], jhelum: [73.73, 32.94], okara: [73.45, 30.81], nawabshah: [68.41, 26.25],
  punjab: [72.7, 31.2], sindh: [68.8, 25.9], balochistan: [66.0, 28.5], 'khyber pakhtunkhwa': [71.8, 34.5], kpk: [71.8, 34.5], lhr: [74.34, 31.55], khi: [67.01, 24.86], isb: [73.05, 33.68], rwp: [73.05, 33.6], fsd: [73.08, 31.42],
  dubai: [55.27, 25.2], 'abu dhabi': [54.37, 24.45], sharjah: [55.41, 25.35], riyadh: [46.68, 24.71], jeddah: [39.19, 21.49], makkah: [39.83, 21.39], madinah: [39.61, 24.47], doha: [51.53, 25.29], 'kuwait city': [47.98, 29.38], muscat: [58.38, 23.59], manama: [50.59, 26.23],
  kabul: [69.17, 34.53], tehran: [51.39, 35.69], delhi: [77.21, 28.61], 'new delhi': [77.21, 28.61], mumbai: [72.88, 19.08], bangalore: [77.59, 12.97], chennai: [80.27, 13.08], kolkata: [88.36, 22.57], dhaka: [90.41, 23.81], colombo: [79.86, 6.93], kathmandu: [85.32, 27.72],
  london: [-0.13, 51.51], manchester: [-2.24, 53.48], birmingham: [-1.9, 52.48], paris: [2.35, 48.86], berlin: [13.4, 52.52], frankfurt: [8.68, 50.11], madrid: [-3.7, 40.42], rome: [12.5, 41.9], amsterdam: [4.9, 52.37], istanbul: [28.98, 41.01], moscow: [37.62, 55.76], cairo: [31.24, 30.04], nairobi: [36.82, -1.29], lagos: [3.38, 6.52], johannesburg: [28.05, -26.2],
  'new york': [-74.01, 40.71], washington: [-77.04, 38.91], chicago: [-87.63, 41.88], houston: [-95.37, 29.76], 'los angeles': [-118.24, 34.05], 'san francisco': [-122.42, 37.77], toronto: [-79.38, 43.65], 'mexico city': [-99.13, 19.43], 'sao paulo': [-46.63, -23.55], 'buenos aires': [-58.38, -34.6],
  singapore: [103.82, 1.35], 'kuala lumpur': [101.69, 3.14], bangkok: [100.5, 13.76], jakarta: [106.85, -6.21], beijing: [116.41, 39.9], shanghai: [121.47, 31.23], 'hong kong': [114.17, 22.32], tokyo: [139.69, 35.69], seoul: [126.98, 37.57], sydney: [151.21, -33.87], melbourne: [144.96, -37.81],
};

let loading = null;
let names = new Map();   // normalised name or ISO code → the map's country name

function decode(topology) {
  const { scale, translate } = topology.transform;
  const arcs = topology.arcs.map((arc) => {
    let x = 0; let y = 0;
    return arc.map(([dx, dy]) => { x += dx; y += dy; return [x * scale[0] + translate[0], y * scale[1] + translate[1]]; });
  });
  const ring = (indexes) => {
    const line = indexes.flatMap((index, at) => {
      const part = index < 0 ? [...arcs[~index]].reverse() : arcs[index];
      return at ? part.slice(1) : part;
    });
    let turn = 0;                // a border that crosses the date line (Russia, Fiji) goes on past 180° instead of back over the whole map
    const out = line.map(([x, y], i) => {
      if (i && x - line[i - 1][0] > 180) turn -= 360;
      if (i && x - line[i - 1][0] < -180) turn += 360;
      return [x + turn, y];
    });
    return Math.min(...out.map((q) => q[0])) < -190 ? out.map(([x, y]) => [x + 360, y]) : out;
  };
  return topology.objects.countries.geometries.filter((g) => g.arcs && g.properties.name !== 'Antarctica').map((g) => ({
    type: 'Feature', id: g.id, properties: { name: g.properties.name },
    geometry: { type: g.type, coordinates: g.type === 'Polygon' ? g.arcs.map(ring) : g.arcs.map((polygon) => polygon.map(ring)) },
  }));
}

/** Load and register the map once ('world'); resolves when a chart may use it. */
export function loadWorld() {
  if (!loading) {
    loading = import('world-atlas/countries-110m.json').then((module) => {
      const features = decode(module.default || module);
      echarts.registerMap('world', { type: 'FeatureCollection', features });
      names = new Map(features.map((f) => [normal(f.properties.name), f.properties.name]));
      for (const [alias, name] of Object.entries(ALIASES)) if (names.has(normal(name))) names.set(alias, name);
      try {     // two-letter codes: PK, AE, GB …
        const english = new Intl.DisplayNames(['en'], { type: 'region' });
        for (let a = 65; a <= 90; a += 1) {
          for (let b = 65; b <= 90; b += 1) {
            const code = String.fromCharCode(a, b);
            const key = normal(english.of(code) || '');
            const name = names.get(key) || names.get(normal(ALIASES[key] || ''));
            if (name) names.set(code.toLowerCase(), name);
          }
        }
      } catch { /* an old browser: names only */ }
    }).catch((error) => { loading = null; throw error; });
  }
  return loading;
}

export const countryOf = (value) => names.get(normal(value)) || null;
export const placeOf = (value) => PLACES[normal(value)] || null;
