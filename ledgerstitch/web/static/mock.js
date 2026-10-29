// LedgerStitch — mock.js : in-browser mock of the local API, enabled with ?mock=1.
// Synthetic data only (no real people, accounts or statements). Used for demos,
// UI development before the backend is ready, and screenshots.
//
// Extra URL switches:  &unlocked=1  start already unlocked
//                      &plan=<plan id>  license plan (default: free)
//                      &dev=1       developer override banner (LEDGERSTITCH_DEV_PLAN)
//                      &empty=1     start with an empty workspace (onboarding screenshots)
//                      &backup=none|old  no encrypted backup yet / last one 45 days ago (shows the reminder banner)
//
// Add-ons: the installed add-ons (GET /addons/index.json, a real request) may ship a mock.js exporting
// mockRoutes(fixtures) -> [{method, path (string | RegExp), handler(req)}]. Their routes are tried first;
// a handler returns a Response, a JSON-able value, or undefined to let the core answer.
//   req = {method, path, url, query, body (parsed JSON), form (FormData | null), match (RegExp match), init}
//   fixtures = see addonFixtures() below.
// A plain public checkout has no add-ons, so its demo shows only the free features.

import { setupRoutes } from './mock-setup.js';           // first-run setup wizard (/api/setup/*)
import { taxDocRoutes } from './mock-taxdocs.js';        // pay stubs and investments (/api/paystubs, /api/investments)

const qs = new URLSearchParams(location.search);
const PLAN = /^[a-z][a-z0-9_]{0,31}$/.test(qs.get('plan') || '') ? qs.get('plan') : 'free';
const DEV = qs.get('dev') === '1';
const CORE_FEATURES = { import: 'Import CSV and PDF statements', reconcile: 'Duplicates, transfers and refunds', rules: 'Rules and built-in keyword categorization',
  review: 'Manual review', vault: 'Encrypted local vaults', 'ai.local': 'Local AI (Ollama on this computer)', reports: 'Summary and Schedule C report',
  exports: 'Excel, CSV and QuickBooks (CSV + IIF) file exports' };
const SIGNUP_URL = 'https://ledgerstitch.com/signup?ref=app-community';   // demo value of status.signup_url
const RECOMMENDED_LOCAL_MODEL = 'glm-4.7-flash:latest';                  // mirrors ai.local_ollama.RECOMMENDED_MODEL
// What the installed add-ons declared (filled from /addons/index.json by loadAddons()).
const ADDONS = { list: [], features: {}, plans: { pro: ['pro'] }, planLabels: {}, allTiersPlan: 'enterprise',
  ui: { screens: [], cards: [], settings: [] }, routes: [], loaded: null };
let EDITION = 'community';
const SOURCE = { url: 'https://github.com/ledgerstitch/ledgerstitch', license: 'AGPL-3.0' };
const EMPTY = qs.get('empty') === '1';
const ONBOARDING = qs.get('onboarding') === '1';

// ---------------------------------------------------------------- deterministic PRNG
function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rnd = mulberry32(20250417);
const between = (a, b) => a + rnd() * (b - a);
const money = (a, b) => (Math.round(between(a, b) * 100) / 100).toFixed(2);
const pick = arr => arr[Math.floor(rnd() * arr.length)];
const pad = n => String(n).padStart(2, '0');
const YEAR = 2025;
const randDate = (m) => { const mm = m ?? (1 + Math.floor(rnd() * 12)); const dd = 1 + Math.floor(rnd() * 27); return `${YEAR}-${pad(mm)}-${pad(dd)}`; };

// ---------------------------------------------------------------- taxonomy (mirrors ledgerstitch/taxonomy.py)
// Generated from ledgerstitch/taxonomy.py — keep in sync.
const CATEGORIES = [
  ["Advertising & Marketing", "business", "L8"],
  ["Software & Subscriptions", "business", "L18"],
  ["Cloud & Hosting", "business", "L18"],
  ["Office Supplies", "business", "L18"],
  ["Equipment & Supplies", "business", "L22"],
  ["Inventory & Cost of Goods", "business", "L4"],
  ["Contractor Payments", "business", "L11"],
  ["Payroll & Wages", "review", "L26"],
  ["Professional Services", "review", "L17"],
  ["Payment Processing Fees", "business", "L10"],
  ["Bank & Merchant Fees", "review", "L27a"],
  ["Interest Expense", "review", "L16b"],
  ["Freight & Shipping", "business", "L27a"],
  ["Telecommunications", "review", "L25"],
  ["Business Travel", "review", "L24a"],
  ["Business Meals", "review", "L24b"],
  ["Education & Training", "business", "L27a"],
  ["Rent & Lease", "review", "L20b"],
  ["Repairs & Maintenance", "review", "L21"],
  ["Taxes & Licenses", "review", "L23"],
  ["Income Tax Payments", "personal", null],
  ["Health Insurance", "personal", null],
  ["Insurance", "review", "L15"],
  ["Vehicle & Fuel", "review", "L9"],
  ["Parking & Tolls", "review", "L9"],
  ["Utilities", "review", "L25"],
  ["Wire & Vendor Payments", "review", "L27a"],
  ["Zelle & P2P Payments", "review", null],
  ["Loan & Mortgage Payments", "review", null],
  ["Groceries", "personal", null],
  ["Dining & Restaurants", "personal", "L24b"],
  ["Shopping", "personal", "L22"],
  ["Home & Garden", "review", "L22"],
  ["Healthcare", "personal", null],
  ["Entertainment & Streaming", "personal", null],
  ["Rideshare & Transit", "review", "L24a"],
  ["Personal Care", "personal", null],
  ["Education (Personal)", "personal", null],
  ["Charity & Donations", "personal", null],
  ["Cash Withdrawal", "personal", null],
  ["Credit Card Payment", "transfer", null],
  ["Transfer Between Accounts", "transfer", null],
  ["Investments & Retirement", "transfer", null],
  ["Refund", "transfer", null],
  ["Business Income (Sales & Payouts)", "income", null],
  ["Paycheck (W-2 Wages)", "income", null],
  ["Interest Income", "income", null],
  ["Income & Deposits", "income", null],
  ["Checks Written", "review", "L27a"],
  ["Uncategorized", "review", null]
].map(([name, lane, schedule_c]) => ({ name, lane, schedule_c, schedule_c_label: null, custom: false }));
const CAT = Object.fromEntries(CATEGORIES.map(c => [c.name, c]));
const SC_NAMES = {
  "L4": "Cost of goods sold (from Part III, line 42)",
  "L8": "Advertising",
  "L9": "Car and truck expenses",
  "L10": "Commissions and fees",
  "L11": "Contract labor",
  "L12": "Depletion",
  "L13": "Depreciation and section 179 expense deduction",
  "L14": "Employee benefit programs (other than on line 19)",
  "L15": "Insurance (other than health)",
  "L16a": "Interest: Mortgage (paid to banks, etc.)",
  "L16b": "Interest: Other",
  "L17": "Legal and professional services",
  "L18": "Office expense",
  "L19": "Pension and profit-sharing plans",
  "L20a": "Rent or lease: Vehicles, machinery, and equipment",
  "L20b": "Rent or lease: Other business property",
  "L21": "Repairs and maintenance",
  "L22": "Supplies (not included in Part III)",
  "L23": "Taxes and licenses",
  "L24a": "Travel",
  "L24b": "Deductible meals",
  "L25": "Utilities",
  "L26": "Wages (less employment credits)",
  "L27a": "Other expenses (from line 48)",
  "L27b": "Energy efficient commercial buildings deduction (Form 7205)"
};

// ---------------------------------------------------------------- state
const state = {
  unlocked: qs.get('unlocked') === '1',
  vaults: [
    { real: 'Rivera Studio LLC', label: 'Rivera Studio LLC', created: '2025-01-06T10:12:00Z', path: '~/LedgerStitch/ws-3f9a21c0.vault', recovery: '2025-01-06' },
    { real: 'Northside Bakery (client)', label: '', created: '2026-02-11T15:40:00Z', path: '~/LedgerStitch/ws-8c02d7e4.vault' },
    { real: 'Personal 2025', label: '', created: '2026-03-02T09:05:00Z', path: '~/LedgerStitch/ws-51be9a03.vault' },
  ],
  vaultName: 'Rivera Studio LLC',
  accounts: [
    { id: 'chase-4012', label: 'Chase Ink Business ••4012', institution: 'Chase', kind: 'credit_card', last4: '4012' },
    { id: 'chase-7781', label: 'Chase Business Checking ••7781', institution: 'Chase', kind: 'checking', last4: '7781' },
    { id: 'amex-1009', label: 'Amex Gold ••1009', institution: 'American Express', kind: 'credit_card', last4: '1009' },
    { id: 'capone-3321', label: 'Capital One Quicksilver ••3321', institution: 'Capital One', kind: 'credit_card', last4: '3321' },
    { id: 'ally-5555', label: 'Ally Tax Savings ••5555', institution: 'Ally', kind: 'savings', last4: '5555' },
  ],
  txns: [],
  rules: [],
  settings: {
    business_name: 'Rivera Studio LLC',
    tax_year: YEAR,
    business_profile: 'Solo brand & web design studio; I work from a home office and travel to clients a few times a year.',
    ai_enabled: false,
    provider: 'off',
    model: '',
    ollama_url: 'http://127.0.0.1:11434',
    has_license_key: PLAN !== 'free',
    auto_lock_minutes: 30,
  },
  lastRun: null,
  license: DEV ? { email: null, plan: PLAN === 'free' ? 'enterprise' : PLAN, expires: null, valid: true }
    : PLAN !== 'free'
      ? { email: 'sam@riverastudio.example', plan: PLAN, expires: '2026-12-31', valid: true }
      : { email: null, plan: 'free', expires: null, valid: false },
  importSeq: 0,
  imports: [],
  // encrypted backups (synthetic): ISO time of the last one
  lastBackupAt: qs.get('backup') === 'none' ? null : new Date(Date.now() - (qs.get('backup') === 'old' ? 45 : 5) * 864e5).toISOString().slice(0, 19) + '+00:00',
};

// Recovery keys look like the real ones (160 random bits, base32 in groups of four); nothing checks them.
function mockRecoveryCode() {
  const A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  const bytes = crypto.getRandomValues(new Uint8Array(20));
  let bits = '', out = '';
  for (const b of bytes) bits += b.toString(2).padStart(8, '0');
  for (let i = 0; i < bits.length; i += 5) out += A[parseInt(bits.slice(i, i + 5), 2)];
  return out.match(/.{4}/g).join('-');
}
function currentVault() { return state.vaults.find(x => x.real === state.vaultName); }
function recoveryOut() { const v = currentVault() || {}; return { supported: true, enabled: !!v.recovery, created: v.recovery || null }; }
function backupOut() {
  const days = state.lastBackupAt ? Math.floor((Date.now() - Date.parse(state.lastBackupAt)) / 864e5) : null;
  const hasData = state.txns.length > 0 || state.accounts.length > 0;
  return { last_backup_at: state.lastBackupAt, days_since: days, due: hasData && (days === null || days > 30), reminder_days: 30 };
}

// ---------------------------------------------------------------- synthetic ledger
let seq = 0;
function txn(o) {
  seq++;
  const c = CAT[o.category] || CAT.Uncategorized;
  const lane = o.lane || (c.lane === 'review' && (o.status || 'auto') !== 'review' ? 'business' : c.lane);
  return {
    id: (o.id || ('t' + seq.toString(16).padStart(6, '0') + Math.floor(rnd() * 1e6).toString(16))).slice(0, 16),
    account_id: o.account_id, date: o.date, description: o.description, amount: o.amount, direction: o.direction || 'out',
    import_id: o.import_id || 'imp-seed', bank_category: o.bank_category || null, merchant: o.merchant,
    category: o.category, lane, schedule_c: lane === 'business' ? (o.schedule_c ?? c.schedule_c) : (o.schedule_c ?? null),
    confidence: o.confidence ?? 0.95, source: o.source || 'rule', status: o.status || 'auto',
    ai_model: o.ai_model ?? ((o.source || 'rule') === 'ai' ? 'ollama:glm-4.7-flash' : null), receipt_id: o.receipt_id || null,
    duplicate_of: o.duplicate_of || null, transfer_group: o.transfer_group || null, refund_of: o.refund_of || null,
    note: o.note || '', seq,
  };
}
function monthly(spec, months = 12) {
  const out = [];
  for (let m = 1; m <= months; m++) {
    const day = spec.day ?? (3 + Math.floor(rnd() * 20));
    out.push(txn({ ...spec, description: typeof spec.description === 'function' ? spec.description() : spec.description, date: `${YEAR}-${pad(m)}-${pad(day)}`, amount: typeof spec.amt === 'string' ? spec.amt : money(...spec.amt) }));
  }
  return out;
}
function random(spec, n) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const acct = Array.isArray(spec.account_id) ? pick(spec.account_id) : spec.account_id;
    const desc = typeof spec.description === 'function' ? spec.description() : spec.description;
    out.push(txn({ ...spec, account_id: acct, description: desc, date: randDate(), amount: money(...spec.amt) }));
  }
  return out;
}

function seed() {
  const T = [];
  const B = (o) => ({ status: 'auto', ...o });
  // --- confident business (auto) ---
  T.push(...monthly(B({ account_id: 'chase-4012', merchant: 'ADOBE', description: 'ADOBE *CREATIVE CLD 800-833-6687 CA', category: 'Software & Subscriptions', amt: '59.99', source: 'rule', confidence: 1, day: 6 })));
  T.push(...monthly(B({ account_id: 'chase-4012', merchant: 'GOOGLE GSUITE', description: 'GOOGLE *GSUITE_riverastud CC@GOOGLE.COM CA', category: 'Software & Subscriptions', amt: '14.40', source: 'heuristic', confidence: 0.9, day: 1 })));
  T.push(...monthly(B({ account_id: 'chase-4012', merchant: 'ZOOM.US', description: 'ZOOM.US 888-799-9666 WWW.ZOOM.US CA', category: 'Software & Subscriptions', amt: '15.99', source: 'heuristic', confidence: 0.9, day: 12 })));
  T.push(...monthly(B({ account_id: 'chase-4012', merchant: 'AMAZON WEB SERVICES', description: 'AMAZON WEB SERVICES AWS.AMAZON.CO WA', category: 'Cloud & Hosting', amt: [18, 64], source: 'rule', confidence: 1, day: 3 })));
  T.push(...monthly(B({ account_id: 'chase-7781', merchant: 'VERIZON WRLS', description: 'VERIZON WRLS P2215-01 PAYMENT', category: 'Telecommunications', amt: '86.40', source: 'rule', confidence: 1, day: 18 })));
  T.push(...monthly(B({ account_id: 'chase-4012', merchant: 'FACEBK ADS', description: 'FACEBK *K7Q2ZMN2D2 FB.ME/ADS CA', category: 'Advertising & Marketing', amt: [45, 160], source: 'ai', confidence: 0.91, day: 22 }), 9));
  T.push(...random(B({ account_id: 'chase-4012', merchant: 'UPWORK', description: 'UPWORK -REF 6638812 HTTPSUPWORK. CA', category: 'Contractor Payments', amt: [240, 920], source: 'ai', confidence: 0.88 }), 4));
  T.push(...random(B({ account_id: ['chase-4012', 'amex-1009'], merchant: 'DELTA AIR LINES', description: () => `DELTA AIR LINES 006${Math.floor(rnd() * 9e6 + 1e6)} ATLANTA`, category: 'Business Travel', amt: [240, 540], source: 'heuristic', confidence: 0.9 }), 3));
  T.push(...random(B({ account_id: 'amex-1009', merchant: 'MARRIOTT', description: 'MARRIOTT AUSTIN DOWNTOWN AUSTIN TX', category: 'Business Travel', amt: [189, 612], source: 'heuristic', confidence: 0.9 }), 2));
  T.push(...random(B({ account_id: 'chase-4012', merchant: 'STAPLES', description: 'STAPLES 00115 PLANO TX', category: 'Office Supplies', amt: [12, 96], source: 'heuristic', confidence: 0.9 }), 4));
  T.push(...random(B({ account_id: 'chase-4012', merchant: 'VISTAPRINT', description: 'VISTAPRINT.COM 866-8936743 MA', category: 'Advertising & Marketing', amt: [38, 140], source: 'heuristic', confidence: 0.9 }), 2));
  T.push(...random(B({ account_id: 'chase-7781', merchant: 'MONTHLY SERVICE FEE', description: 'MONTHLY SERVICE FEE', category: 'Bank & Merchant Fees', lane: 'business', schedule_c: 'L27a', amt: [15, 15], source: 'heuristic', confidence: 0.9 }), 3));
  T.push(...random({ account_id: 'chase-4012', merchant: 'CANVA', description: 'CANVA* I04220-881122 SYDNEY', category: 'Software & Subscriptions', amt: [14.99, 14.99], source: 'user', confidence: 1, status: 'approved' }, 2));
  T.push(...monthly(B({ account_id: 'chase-7781', merchant: 'STRIPE FEE', description: 'STRIPE FEE BILLING ST-FEES', category: 'Payment Processing Fees', amt: [38, 96], source: 'heuristic', confidence: 0.9, day: 2 })));
  // --- confident personal ---
  T.push(...monthly(B({ account_id: 'capone-3321', merchant: 'NETFLIX.COM', description: 'NETFLIX.COM NETFLIX.COM CA', category: 'Entertainment & Streaming', amt: '15.49', source: 'heuristic', confidence: 0.9, day: 9 })));
  T.push(...monthly(B({ account_id: 'capone-3321', merchant: 'SPOTIFY', description: 'SPOTIFY USA 877-7781261 NY', category: 'Entertainment & Streaming', amt: '11.99', source: 'heuristic', confidence: 0.9, day: 14 })));
  T.push(...random(B({ account_id: 'capone-3321', merchant: 'KROGER', description: () => `KROGER #${pick(['0541', '0388', '0712'])} FRISCO TX`, category: 'Groceries', amt: [38, 186], source: 'heuristic', confidence: 0.9 }), 14));
  T.push(...random(B({ account_id: 'amex-1009', merchant: 'STARBUCKS', description: () => `STARBUCKS STORE ${Math.floor(rnd() * 9e4 + 1e4)} DALLAS TX`, category: 'Dining & Restaurants', amt: [4.25, 9.8], source: 'heuristic', confidence: 0.86 }), 12));
  T.push(...random(B({ account_id: 'capone-3321', merchant: 'CVS PHARMACY', description: 'CVS/PHARMACY #07012 FRISCO TX', category: 'Healthcare', amt: [8, 64], source: 'heuristic', confidence: 0.9 }), 4));
  // --- transfers & card payments ---
  for (const [acct, label] of [['chase-4012', 'CHASE CREDIT CRD AUTOPAY'], ['amex-1009', 'AMEX EPAYMENT ACH PMT'], ['capone-3321', 'CAPITAL ONE MOBILE PMT']]) {
    for (let m = 1; m <= 12; m++) {
      const amt = money(300, 2200), g = `tg-${acct}-${m}`, date = `${YEAR}-${pad(m)}-25`;
      T.push(txn({ account_id: acct, merchant: 'PAYMENT THANK YOU', description: 'PAYMENT THANK YOU - WEB', category: 'Credit Card Payment', amount: amt, direction: 'in', date, transfer_group: g, source: 'heuristic', confidence: 0.99 }));
      T.push(txn({ account_id: 'chase-7781', merchant: label, description: `${label} ${Math.floor(rnd() * 9e8 + 1e8)}`, category: 'Credit Card Payment', amount: amt, direction: 'out', date, transfer_group: g, source: 'heuristic', confidence: 0.99 }));
    }
  }
  // --- income ---
  for (let m = 1; m <= 12; m++) for (let k = 0; k < 2; k++) {
    T.push(txn({ account_id: 'chase-7781', merchant: 'STRIPE TRANSFER', description: `STRIPE TRANSFER ST-${Math.random().toString(36).slice(2, 10).toUpperCase()}`, category: 'Business Income (Sales & Payouts)', amount: money(1400, 5200), direction: 'in', date: `${YEAR}-${pad(m)}-${pad(k ? 20 : 6)}`, source: 'heuristic', confidence: 0.95 }));
  }
  // --- needs review (grouped by merchant in the Review queue) ---
  const R = (o) => ({ status: 'review', ...o });
  T.push(...random(R({ account_id: ['capone-3321', 'chase-4012'], merchant: 'AMAZON MKTPL', description: () => `AMAZON MKTPL*${Math.random().toString(36).slice(2, 11).toUpperCase()} Amzn.com/bill WA`, category: 'Shopping', amt: [11, 148], source: 'heuristic', confidence: 0.55 }), 14));
  T.push(...random(R({ account_id: 'capone-3321', merchant: 'COSTCO WHSE', description: 'COSTCO WHSE #0687 PLANO TX', category: 'Groceries', amt: [82, 264], source: 'heuristic', confidence: 0.62 }), 6));
  T.push(...random(R({ account_id: ['chase-4012', 'amex-1009'], merchant: 'UBER TRIP', description: 'UBER *TRIP HELP.UBER.COM CA', category: 'Rideshare & Transit', lane: 'review', amt: [11, 48], source: 'ai', confidence: 0.62 }), 9));
  T.push(...random(R({ account_id: 'capone-3321', merchant: 'THE HOME DEPOT', description: 'THE HOME DEPOT #6512 FRISCO TX', category: 'Home & Garden', amt: [18, 310], source: 'heuristic', confidence: 0.58 }), 4));
  T.push(...random(R({ account_id: 'chase-7781', merchant: 'ZELLE TO <PERSON>', description: () => `Zelle payment to ${pick(['Marco D', 'Priya N', 'Lena K'])} JPM99${Math.floor(rnd() * 9e6)}`, category: 'Zelle & P2P Payments', lane: 'review', amt: [250, 650], source: 'heuristic', confidence: 0.3 }), 4));
  T.push(...random(R({ account_id: 'amex-1009', merchant: 'TST* BLUE BOTTLE', description: 'TST* BLUE BOTTLE COFFEE DALLAS TX', category: 'Dining & Restaurants', amt: [6, 38], source: 'heuristic', confidence: 0.66 }), 7));
  T.push(...random(R({ account_id: 'capone-3321', merchant: 'WAL-MART', description: () => `WAL-MART #${pick(['3418', '0921'])} MCKINNEY TX`, category: 'Groceries', amt: [14, 132], source: 'heuristic', confidence: 0.6 }), 5));
  T.push(...random(R({ account_id: 'chase-4012', merchant: 'SHELL OIL', description: () => `SHELL OIL ${Math.floor(rnd() * 9e4 + 1e4)} FRISCO TX`, category: 'Vehicle & Fuel', lane: 'review', amt: [34, 71], source: 'heuristic', confidence: 0.5 }), 8));
  T.push(...random(R({ account_id: 'capone-3321', merchant: 'APPLE.COM/BILL', description: 'APPLE.COM/BILL 866-712-7753 CA', category: 'Entertainment & Streaming', amt: [2.99, 9.99], source: 'heuristic', confidence: 0.7 }), 4));
  T.push(txn(R({ account_id: 'chase-4012', merchant: 'BEST BUY', description: 'BEST BUY 00004812 PLANO TX', category: 'Shopping', amount: '1299.99', date: `${YEAR}-03-14`, source: 'heuristic', confidence: 0.52 })));
  T.push(...random(R({ account_id: 'chase-4012', merchant: 'PAYPAL *INKWELLDESIGN', description: 'PAYPAL *INKWELLDESIGN 402-935-7733 CA', category: 'Zelle & P2P Payments', lane: 'review', amt: [180, 180], source: 'ai', confidence: 0.44 }), 2));
  T.push(...monthly(R({ account_id: 'chase-7781', merchant: 'GEICO', description: 'GEICO *AUTO 800-841-3000 DC', category: 'Insurance', lane: 'review', amt: '128.40', source: 'heuristic', confidence: 0.5, day: 11 })));
  T.push(...monthly(R({ account_id: 'chase-4012', merchant: 'WEWORK', description: 'WEWORK MEMBERSHIPS 646-389-3922 NY', category: 'Rent & Lease', lane: 'business', amt: '350.00', source: 'ai', confidence: 0.71, day: 1 }), 6));
  T.push(...monthly(R({ account_id: 'chase-4012', merchant: 'LINKEDIN PRE', description: () => 'LINKEDIN PRE *8812731 LNKD.IN/BILL CA', category: 'Software & Subscriptions', lane: 'business', amt: '39.99', source: 'ai', confidence: 0.73, day: 19 }), 8));
  T.push(...random(R({ account_id: 'amex-1009', merchant: 'SQ *GREENLEAF KITCHEN', description: 'SQ *GREENLEAF KITCHEN DALLAS TX', category: 'Uncategorized', lane: 'review', amt: [24, 96], source: 'none', confidence: 0 }), 3));
  T.push(...random(B({ account_id: 'amex-1009', merchant: 'TST* OAK & ASH', description: 'TST* OAK & ASH DALLAS TX', category: 'Business Meals', lane: 'business', schedule_c: 'L24b', amt: [38, 142], source: 'ai', confidence: 0.84 }), 6));
  // --- a refund and a duplicate (both visible in the ledger) ---
  const bb = T.find(t => t.merchant === 'STAPLES');
  if (bb) T.push(txn({ account_id: bb.account_id, merchant: 'STAPLES', description: 'STAPLES 00115 PLANO TX RETURN', category: bb.category, amount: (num(bb.amount) / 2).toFixed(2), direction: 'in', date: bb.date, refund_of: bb.id, source: 'heuristic', confidence: 0.9 }));
  const ad = T.find(t => t.merchant === 'ADOBE');
  if (ad) T.push(txn({ ...ad, id: undefined, import_id: 'imp-seed-2', duplicate_of: ad.id, note: '' }));
  // --- subscriptions demo: an Adobe price increase, a new (second) design tool, monthly tax savings ---
  for (const t of T) if (t.merchant === 'ADOBE' && !t.duplicate_of && t.date >= `${YEAR}-10-01`) t.amount = '64.99';
  T.push(...[11, 12].map(m => txn({ account_id: 'chase-4012', merchant: 'FIGMA', description: 'FIGMA MONTHLY FIGMA.COM CA', category: 'Software & Subscriptions', amount: '15.00', date: `${YEAR}-${pad(m)}-04`, source: 'heuristic', confidence: 0.9 })));
  for (let m = 1; m <= 12; m++) {
    const g = `tg-tax-${m}`, date = `${YEAR}-${pad(m)}-27`, amount = m % 4 === 0 ? '600.00' : '900.00';
    T.push(txn({ account_id: 'ally-5555', merchant: 'TRANSFER FROM CHECKING', description: 'ONLINE TRANSFER FROM CHK ...7781', category: 'Transfer Between Accounts', amount, direction: 'in', date, transfer_group: g, source: 'heuristic', confidence: 0.99 }));
    T.push(txn({ account_id: 'chase-7781', merchant: 'TRANSFER TO SAVINGS', description: 'ONLINE TRANSFER TO SAV ...5555', category: 'Transfer Between Accounts', amount, direction: 'out', date, transfer_group: g, source: 'heuristic', confidence: 0.99 }));
  }
  T.sort((a, b) => a.date < b.date ? 1 : a.date > b.date ? -1 : 0);
  return T;
}
function out(t) {
  const a = state.accounts.find(x => x.id === t.account_id);
  return { ...t, account_label: a ? a.label : t.account_id, signed_amount: (t.direction === 'out' ? '-' : '') + t.amount };
}
function num(v) { const n = parseFloat(v); return Number.isFinite(n) ? n : 0; }

const SEED_ACCOUNTS = state.accounts.map(a => ({ ...a }));
state.onboarding = { completed: !ONBOARDING, local_ai_model: null, local_ai_requested: null };
if (ONBOARDING) { state.vaults = []; state.unlocked = false; }
function onboardingOut() {
  return { completed: state.onboarding.completed, needed: !state.onboarding.completed && state.vaults.length === 0,
    local_ai_model: state.onboarding.local_ai_model, local_ai_requested: state.onboarding.local_ai_requested };
}
/** "Try with sample data": a separate, visibly labelled sample workspace with the synthetic ledger. */
function loadDemo() {
  const name = 'Sample data (Rivera Studio)';
  if (!state.vaults.some(v => v.real === name)) {
    state.vaults.push({ real: name, label: 'Sample data', created: new Date().toISOString(), path: `~/LedgerStitch/ws-${Math.random().toString(16).slice(2, 10)}.vault` });
  }
  state.unlocked = true; state.vaultName = name;
  state.accounts = SEED_ACCOUNTS.map(a => ({ ...a }));
  state.txns = seed();
  state.settings.business_name = 'Rivera Studio LLC (sample)';
  return statusPayload();
}

if (!EMPTY) {
  state.txns = seed();
  // Imported files (synthetic): one per account, so the Import screen, balances and "delete file" have data.
  const SEED_FILES = [
    ['chase-4012', 'Chase4012_Activity_2025.csv', 'Chase', '2025-12-28T15:10:00+00:00', true, '0.00'],
    ['chase-7781', 'chase_checking_7781_ytd.ofx', 'Chase', '2025-12-28T15:12:00+00:00', true, '0.00'],
    ['amex-1009', 'amex_gold_2025.pdf', 'American Express', '2025-12-28T15:15:00+00:00', true, '0.00'],
    ['capone-3321', 'CapitalOne_Sep_2025.pdf', 'Capital One', '2025-12-28T15:17:00+00:00', false, '15.49'],
    ['ally-5555', 'ally_savings_2025.csv', 'Ally', '2025-12-28T15:20:00+00:00', null, null],
  ];
  for (const [acct, filename, institution, date, reconciled, diff] of SEED_FILES) {
    const import_id = `imp-seed-${acct}`;
    for (const t of state.txns) if (t.account_id === acct && t.import_id === 'imp-seed') t.import_id = import_id;
    state.imports.push({ import_id, filename, institution, account_id: acct, date, count: 0, new: 0, warnings: reconciled === false ? ['reconciliation: parsed money-out differs from the statement total by 15.49'] : [], reconciled, reconcile_difference: diff });
  }
  for (const i of state.imports) { i.count = state.txns.filter(t => t.import_id === i.import_id).length; i.new = i.count; }
  state.rules = [
    { id: 'r-adobe', pattern: 'ADOBE', category: 'Software & Subscriptions', lane: 'business', schedule_c: 'L18', field: 'merchant', account_id: null, created_by: 'user' },
    { id: 'r-aws', pattern: 'AMAZON WEB SERVICES', category: 'Cloud & Hosting', lane: 'business', schedule_c: 'L18', field: 'merchant', account_id: null, created_by: 'user' },
    { id: 'r-verizon', pattern: 'VERIZON WRLS', category: 'Telecommunications', lane: 'business', schedule_c: 'L25', field: 'merchant', account_id: 'chase-7781', created_by: 'rule' },
    { id: 'r-canva', pattern: 'CANVA', category: 'Software & Subscriptions', lane: 'business', schedule_c: 'L18', field: 'merchant', account_id: null, created_by: 'user' },
  ];
} else {
  state.accounts = [];
}

// ---------------------------------------------------------------- summary (shape per tech lead)
function summarize(year) {
  const y = year ? String(year) : null;
  const rows = state.txns.filter(t => !t.duplicate_of && (!y || t.date.startsWith(y)));
  const totals = { business: 0, personal: 0, review: 0, expenses: 0, excluded: 0, transfers_out: 0, transfers_in: 0, income: 0 };
  const byCat = new Map(), bySc = new Map(), byMonth = new Map(), byAcct = new Map();
  const refunds = new Map();
  for (const t of rows) if (t.refund_of) refunds.set(t.refund_of, (refunds.get(t.refund_of) || 0) + num(t.amount));
  for (const t of rows) {
    const a = num(t.amount);
    const month = t.date.slice(0, 7);
    if (!byMonth.has(month)) byMonth.set(month, { month, business: 0, personal: 0, review: 0, income: 0 });
    const mo = byMonth.get(month);
    const acc = byAcct.get(t.account_id) || { account_id: t.account_id, business: 0, personal: 0, review: 0, transfers_out: 0, transfers_in: 0, income: 0, count: 0 };
    acc.count++; byAcct.set(t.account_id, acc);
    if (t.lane === 'transfer') acc[t.direction === 'out' ? 'transfers_out' : 'transfers_in'] += a;
    else if (t.lane === 'income') { if (t.direction === 'in') acc.income += a; }
    else if (t.direction === 'out' && !t.refund_of) acc[t.lane] += a;
    if (t.refund_of) continue;
    if (t.lane === 'transfer') { t.direction === 'out' ? totals.transfers_out += a : totals.transfers_in += a; totals.excluded += a; continue; }
    if (t.lane === 'income') { if (t.direction === 'in') { totals.income += a; mo.income += a; } continue; }
    if (t.direction !== 'out') continue;
    const net = a - (refunds.get(t.id) || 0);
    totals[t.lane] += net; mo[t.lane] += net;
    const ck = t.category + '|' + t.lane;
    const c = byCat.get(ck) || { category: t.category, lane: t.lane, schedule_c: t.lane === 'business' ? t.schedule_c : null, total: 0, count: 0 };
    c.total += net; c.count++; byCat.set(ck, c);
    if (t.lane === 'business' && t.schedule_c) {
      const s = bySc.get(t.schedule_c) || { line: t.schedule_c, name: SC_NAMES[t.schedule_c] || t.schedule_c, gross: 0, deductible_pct: t.schedule_c === 'L24b' ? 0.5 : 1.0, deductible: 0, count: 0 };
      s.gross += net; s.count++; s.deductible = s.gross * s.deductible_pct; bySc.set(t.schedule_c, s);
    }
  }
  totals.expenses = totals.business;
  const r2 = v => Math.round(v * 100) / 100;
  for (const k of Object.keys(totals)) totals[k] = r2(totals[k]);
  const lineOrder = l => { const m = l.match(/L(\d+)([a-z]?)/); return m ? +m[1] * 10 + (m[2] ? m[2].charCodeAt(0) - 96 : 0) : 999; };
  const by_schedule_c = [...bySc.values()].sort((a, b) => lineOrder(a.line) - lineOrder(b.line)).map(s => ({ ...s, gross: r2(s.gross), deductible: r2(s.deductible) }));
  const review_count = rows.filter(t => t.status === 'review').length;
  return {
    year: year ? +year : null,
    totals,
    by_category: [...byCat.values()].map(c => ({ ...c, total: r2(c.total) })).sort((a, b) => b.total - a.total),
    by_schedule_c,
    schedule_c_total: { gross: r2(by_schedule_c.reduce((s, x) => s + x.gross, 0)), deductible: r2(by_schedule_c.reduce((s, x) => s + x.deductible, 0)) },
    by_account: [...byAcct.values()].map(a => Object.fromEntries(Object.entries(a).map(([k, v]) => [k, typeof v === 'number' && k !== 'count' ? r2(v) : v]))),
    by_month: [...byMonth.values()].sort((a, b) => a.month < b.month ? -1 : 1).map(m => ({ month: m.month, business: r2(m.business), personal: r2(m.personal), review: r2(m.review), income: r2(m.income) })),
    counts: {
      total: rows.length,
      business: rows.filter(t => t.lane === 'business').length,
      personal: rows.filter(t => t.lane === 'personal').length,
      review: rows.filter(t => t.lane === 'review').length,
      transfer: rows.filter(t => t.lane === 'transfer').length,
      income: rows.filter(t => t.lane === 'income').length,
      duplicates: state.txns.filter(t => t.duplicate_of).length,
      needs_review: review_count,
      uncategorized: rows.filter(t => !t.category || t.category === 'Uncategorized').length,
    },
    review_count,
    uncategorized_count: rows.filter(t => !t.category || t.category === 'Uncategorized').length,
    disclaimer: 'LedgerStitch organizes your statements for review by you and your tax professional. It is not tax, legal or accounting advice.',
    meals_note: 'Business meals are generally 50% deductible; the Deductible column applies that limit.',
  };
}

// The demo ledger only holds 2025 rows. For year-over-year deltas (Overview KPIs) the prior year is a
// synthetic, totals-only summary: 2025 scaled down a little with a stable per-month wobble.
function priorYearSummary() {
  const cur = summarize(YEAR);
  const f = { income: 0.893, business: 0.936, personal: 0.95, review: 0.9 };
  const wob = i => 1 + Math.sin(i * 1.7) * 0.06;
  const r2 = v => Math.round(v * 100) / 100;
  const by_month = cur.by_month.map((m, i) => ({ month: `${YEAR - 1}${m.month.slice(4)}`, business: r2(m.business * f.business * wob(i)),
    personal: r2(m.personal * f.personal * wob(i + 3)), review: r2(m.review * f.review), income: r2(m.income * f.income * wob(i + 5)) }));
  const sum = k => r2(by_month.reduce((a, m) => a + m[k], 0));
  const totals = { ...cur.totals, income: sum('income'), business: sum('business'), personal: sum('personal'), review: sum('review') };
  totals.expenses = totals.business;
  return { ...cur, year: YEAR - 1, totals, by_month, by_category: [], by_schedule_c: [], by_account: [], review_count: 0,
    counts: { ...cur.counts, needs_review: 0, review: 0 }, synthetic_prior_year: true };
}

// ---------------------------------------------------------------- receipts (synthetic)
const TINY_RECEIPT = (title, lines, total) => {
  const esc = v => String(v).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]);
  const rows = lines.map(([a, b], i) => `<text x="16" y="${92 + i * 18}" font-size="11">${esc(a)}</text><text x="224" y="${92 + i * 18}" font-size="11" text-anchor="end">${esc(b)}</text>`).join('');
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="240" height="320" viewBox="0 0 240 320"><rect width="240" height="320" fill="#fbfaf6"/><g font-family="monospace" fill="#333">` +
    `<text x="120" y="36" font-size="15" text-anchor="middle" font-weight="bold">${esc(title)}</text><text x="120" y="56" font-size="10" text-anchor="middle">Thank you!</text>` +
    `<line x1="16" y1="70" x2="224" y2="70" stroke="#999" stroke-dasharray="3 3"/>${rows}` +
    `<line x1="16" y1="${100 + lines.length * 18}" x2="224" y2="${100 + lines.length * 18}" stroke="#999" stroke-dasharray="3 3"/>` +
    `<text x="16" y="${120 + lines.length * 18}" font-size="13" font-weight="bold">TOTAL</text><text x="224" y="${120 + lines.length * 18}" font-size="13" text-anchor="end" font-weight="bold">${esc(total)}</text></g></svg>`;
  return svg;
};
function seedReceipts() {
  const find = (m, pred = () => true) => state.txns.find(t => t.merchant === m && pred(t));
  const staples = find('STAPLES', t => t.direction === 'out' && !t.refund_of);
  const delta = find('DELTA AIR LINES');
  const bb = find('BEST BUY');
  const shells = state.txns.filter(t => t.merchant === 'SHELL OIL').slice(0, 2);
  const cand = (t, score, reasons) => t && { txn_id: t.id, score, reasons, date: t.date, description: t.description, amount: (t.direction === 'out' ? '-' : '') + t.amount, account_label: (state.accounts.find(a => a.id === t.account_id) || {}).label || t.account_id };
  const plusDays = (d, n) => { const x = new Date(d + 'T00:00:00Z'); x.setUTCDate(x.getUTCDate() + n); return x.toISOString().slice(0, 10); };
  const R = [];
  if (staples) R.push({ id: 'rc-staples', filename: 'IMG_4471.HEIC', mime: 'image/svg+xml', added: '2026-10-12', size: 1840221, merchant: 'Staples', date: staples.date, total: num(staples.amount),
    subtotal: Math.round(num(staples.amount) / 1.0825 * 100) / 100, tax: Math.round((num(staples.amount) - num(staples.amount) / 1.0825) * 100) / 100, tip: null,
    card_last4: '4012', card_brand: 'Visa', payment_method: 'card', ocr_engine: 'Apple Vision', ocr_confidence: 0.94, status: 'matched', txn_id: staples.id, match_score: 0.97,
    match_reasons: ['Amount matches exactly', 'Card ••4012 matches', 'Same day'], candidates: [cand(staples, 0.97, ['Amount matches exactly', 'Card ••4012 matches', 'Same day'])],
    _svg: TINY_RECEIPT('STAPLES #00115', [['Copy paper 5rm', ''], ['Pens, black 12pk', ''], ['Subtotal', '']], `$${staples.amount}`) });
  if (delta) R.push({ id: 'rc-delta', filename: 'delta-eticket-receipt.pdf', mime: 'application/pdf', added: '2026-10-12', size: 88214, merchant: 'Delta Air Lines', date: plusDays(delta.date, -1), total: num(delta.amount),
    subtotal: null, tax: null, tip: null, card_last4: delta.account_id === 'amex-1009' ? '1009' : '4012', card_brand: delta.account_id === 'amex-1009' ? 'Amex' : 'Visa', payment_method: 'card',
    ocr_engine: 'PDF text', ocr_confidence: 0.99, status: 'manual', txn_id: delta.id, match_score: null, match_reasons: ['Linked by you'], candidates: [cand(delta, 0.9, ['Amount matches exactly', '1 day after receipt'])] });
  if (bb) R.push({ id: 'rc-bestbuy', filename: 'IMG_4502.jpg', mime: 'image/svg+xml', added: '2026-10-14', size: 2210933, merchant: 'Best Buy', date: plusDays(bb.date, -1), total: num(bb.amount),
    subtotal: 1200.92, tax: 99.07, tip: null, card_last4: '4012', card_brand: 'Visa', payment_method: 'card', ocr_engine: 'Apple Vision', ocr_confidence: 0.88, status: 'suggested', txn_id: null, match_score: 0.81,
    match_reasons: [], candidates: [cand(bb, 0.81, ['Amount matches exactly', 'Card ••4012 matches', '1 day after receipt'])],
    _svg: TINY_RECEIPT('BEST BUY #4812', [['MacBook sleeve', '$49.99'], ['USB-C dock', '$189.99'], ['27in monitor', '$960.94'], ['Tax', '$99.07']], `$${bb.amount}`) });
  if (shells.length) R.push({ id: 'rc-shell', filename: 'fuel-receipt.png', mime: 'image/svg+xml', added: '2026-10-15', size: 640112, merchant: 'Shell', date: shells[0].date, total: Math.round((num(shells[0].amount) + 0.4) * 100) / 100,
    subtotal: null, tax: null, tip: null, card_last4: null, card_brand: null, payment_method: 'card', ocr_engine: 'Tesseract', ocr_confidence: 0.61, status: 'unmatched', txn_id: null, match_score: null, match_reasons: [],
    candidates: shells.map((t, i) => cand(t, i ? 0.42 : 0.58, i ? ['Same merchant', '6 days apart'] : ['Same merchant', 'Same day', 'Amount within $0.40'])),
    _svg: TINY_RECEIPT('SHELL 31877', [['Pump 6 Unleaded', ''], ['11.204 gal @ 3.49', '']], `$${(num(shells[0].amount) + 0.4).toFixed(2)}`) });
  R.push({ id: 'rc-blurry', filename: 'IMG_4519.HEIC', mime: 'image/svg+xml', added: '2026-10-16', size: 1502877, merchant: null, date: null, total: null, subtotal: null, tax: null, tip: null,
    card_last4: null, card_brand: null, payment_method: null, ocr_engine: 'Apple Vision', ocr_confidence: 0.08, status: 'no_text', txn_id: null, match_score: null, match_reasons: [], candidates: [],
    _svg: '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="320"><defs><filter id="b"><feGaussianBlur stdDeviation="6"/></filter></defs><rect width="240" height="320" fill="#d9d4c7"/><g filter="url(#b)" fill="#888"><rect x="30" y="40" width="180" height="14"/><rect x="30" y="90" width="150" height="10"/><rect x="30" y="120" width="170" height="10"/><rect x="30" y="150" width="120" height="10"/></g></svg>' });
  for (const r of R) if (r.txn_id) { const t = state.txns.find(x => x.id === r.txn_id); if (t) t.receipt_id = r.id; }
  return R;
}
function rcOut(r) { const { _svg, _blob, ...rest } = r; void _svg; void _blob; return rest; }
function rematch(r) {
  for (const t of state.txns) if (t.receipt_id === r.id && t.id !== r.txn_id) t.receipt_id = null;
  if (r.status === 'manual') return r;
  const tot = num(r.total);
  const scored = [];
  if (r.total != null) {
    for (const t of state.txns) {
      if (t.direction !== 'out' || t.duplicate_of) continue;
      const diff = Math.abs(num(t.amount) - tot);
      if (diff > 1) continue;
      const reasons = [diff < 0.005 ? 'Amount matches exactly' : `Amount within $${diff.toFixed(2)}`];
      let score = diff < 0.005 ? 0.6 : 0.35;
      const acct = state.accounts.find(a => a.id === t.account_id);
      if (r.card_last4 && acct && acct.last4 === r.card_last4) { score += 0.25; reasons.push(`Card ••${r.card_last4} matches`); }
      if (r.date) {
        const days = Math.round((new Date(t.date) - new Date(r.date)) / 86400000);
        if (Math.abs(days) > 5) continue;
        score += days === 0 ? 0.15 : 0.1 - Math.abs(days) * 0.015;
        reasons.push(days === 0 ? 'Same day' : `${Math.abs(days)} day${Math.abs(days) === 1 ? '' : 's'} ${days > 0 ? 'after' : 'before'} receipt`);
      }
      scored.push({ txn_id: t.id, score: Math.round(Math.min(0.99, score) * 100) / 100, reasons, date: t.date, description: t.description, amount: '-' + t.amount, account_label: acct ? acct.label : t.account_id });
    }
  }
  scored.sort((a, b) => b.score - a.score);
  r.candidates = scored.slice(0, 4);
  const best = r.candidates[0];
  if (best && best.score >= 0.9) { r.status = 'matched'; r.txn_id = best.txn_id; r.match_score = best.score; r.match_reasons = best.reasons; }
  else { r.txn_id = null; r.match_score = best ? best.score : null; r.match_reasons = []; r.status = !r.merchant && r.total == null ? 'no_text' : best ? 'suggested' : 'unmatched'; }
  if (r.txn_id) { const t = state.txns.find(x => x.id === r.txn_id); if (t) t.receipt_id = r.id; }
  return r;
}

state.receipts = EMPTY ? [] : seedReceipts();

// ---------------------------------------------------------------- helpers
function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), { status, headers: { 'content-type': 'application/json', ...headers } });
}
const err = (status, error, extra = {}) => json({ error, ...extra }, status);
const sleep = ms => new Promise(r => setTimeout(r, ms));
function bodyOf(init) { try { return init && typeof init.body === 'string' ? JSON.parse(init.body) : {}; } catch { return {}; } }
function filesOf(form) {
  if (!form || typeof form.getAll !== 'function') return [];
  return [...form.getAll('file'), ...form.getAll('files'), ...form.getAll('file[]')].filter(f => f && typeof f === 'object' && 'name' in f);
}
function applyFields(t, b) {
  if (b.category !== undefined && b.category !== null) {
    t.category = b.category;
    if (b.lane === undefined) { const c = CAT[b.category]; if (c && c.lane !== 'review') t.lane = c.lane; }
    if (b.schedule_c === undefined) t.schedule_c = (CAT[b.category] || {}).schedule_c || null;
  }
  if (b.lane !== undefined && b.lane !== null) t.lane = b.lane;
  if (b.schedule_c !== undefined) t.schedule_c = b.schedule_c;
  if (b.note !== undefined) t.note = b.note;
  if (b.status !== undefined && b.status !== null) t.status = b.status;
  if (b.category !== undefined || b.lane !== undefined) { t.source = 'user'; t.confidence = 1; t.ai_model = null; }
}
function addRuleFrom(t) {
  const id = 'r-' + Math.random().toString(36).slice(2, 10);
  state.rules = state.rules.filter(r => r.pattern.toUpperCase() !== String(t.merchant).toUpperCase());
  state.rules.unshift({ id, pattern: t.merchant, category: t.category, lane: t.lane, schedule_c: t.schedule_c, field: 'merchant', account_id: null, created_by: 'user' });
}
function parseCSV(text, maxRows = 9) {
  const rows = [];
  let row = [], cur = '', q = false;
  for (let i = 0; i < text.length && rows.length < maxRows; i++) {
    const ch = text[i];
    if (q) {
      if (ch === '"' && text[i + 1] === '"') { cur += '"'; i++; }
      else if (ch === '"') q = false;
      else cur += ch;
    } else if (ch === '"') q = true;
    else if (ch === ',') { row.push(cur); cur = ''; }
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++;
      row.push(cur); cur = '';
      if (row.some(c => c.trim())) rows.push(row);
      row = [];
    } else cur += ch;
  }
  if ((cur || row.length) && rows.length < maxRows) { row.push(cur); if (row.some(c => c.trim())) rows.push(row); }
  return rows;
}
function detectFrom(name, header) {
  const n = name.toLowerCase(), hdr = header.map(x => x.toLowerCase().trim()).join('|');
  if (n.endsWith('.pdf')) return { id: 'pdf-statement', institution: n.includes('chase') ? 'Chase' : 'Bank of America', kind: 'checking', confidence: 0.82, account_hint: '5521', pdf: true };
  if (n.includes('chase') || hdr.includes('transaction date|post date|description|category|type|amount')) return { id: 'chase-card', institution: 'Chase', kind: 'credit_card', confidence: 0.97, account_hint: '4012' };
  if (n.includes('amex') || hdr.includes('date|description|amount') && hdr.includes('extended details')) return { id: 'amex', institution: 'American Express', kind: 'credit_card', confidence: 0.95, account_hint: '1009' };
  if (n.includes('capital') || hdr.includes('posted date|card no.')) return { id: 'capone-card', institution: 'Capital One', kind: 'credit_card', confidence: 0.94, account_hint: '3321' };
  if (n.includes('citi')) return { id: 'citi', institution: 'Citi', kind: 'credit_card', confidence: 0.93, account_hint: '8820' };
  if (n.includes('wells') || n.includes('boa') || n.includes('discover') || n.includes('apple')) return { id: 'generic-known', institution: n.includes('wells') ? 'Wells Fargo' : n.includes('boa') ? 'Bank of America' : n.includes('discover') ? 'Discover' : 'Apple Card', kind: 'credit_card', confidence: 0.9, account_hint: null };
  if (/date/.test(hdr) && /amount/.test(hdr) && /desc/.test(hdr)) return { id: 'generic', institution: 'Unknown bank', kind: 'other', confidence: 0.78, account_hint: null };
  return { id: 'generic', institution: 'Unknown bank', kind: 'other', confidence: 0.31, account_hint: null };
}
function addImportedRows(accountId, count, importId) {
  const merchants = [
    ['NOTION LABS', 'NOTION LABS, INC. NOTION.SO CA', 'Software & Subscriptions', 'auto', 0.92, 'ai'],
    ['TRADER JOE', "TRADER JOE'S #417 DALLAS TX", 'Groceries', 'auto', 0.9, 'heuristic'],
    ['FEDEX', 'FEDEX OFFIC52100005215 PLANO TX', 'Freight & Shipping', 'auto', 0.9, 'heuristic'],
    ['SQ *DOWNTOWN DELI', 'SQ *DOWNTOWN DELI DALLAS TX', 'Dining & Restaurants', 'review', 0.55, 'heuristic'],
    ['AMAZON MKTPL', 'AMAZON MKTPL*ZX81Q2 Amzn.com/bill WA', 'Shopping', 'review', 0.55, 'heuristic'],
  ];
  for (let i = 0; i < count; i++) {
    const [m, d, cat, status, conf, src] = pick(merchants);
    state.txns.unshift(txn({ account_id: accountId, merchant: m, description: d, category: cat, amount: money(6, 220), date: randDate(), status, confidence: conf, source: src, import_id: importId }));
  }
}
function ensureAccount(det, label) {
  const last4 = det.account_hint || String(Math.floor(rnd() * 9000 + 1000));
  const id = `${det.institution.toLowerCase().replace(/[^a-z]+/g, '-').replace(/^-|-$/g, '')}-${last4}`;
  let acct = state.accounts.find(a => a.id === id);
  if (!acct) {
    acct = { id, label: label || `${det.institution} ••${last4}`, institution: det.institution, kind: det.kind || 'other', last4 };
    state.accounts.push(acct);
  }
  return acct;
}
function currentPlan() { return state.unlocked && state.license.valid ? state.license.plan : 'free'; }
function tierUnlocked(tier, plan) {
  if (tier === 'community') return true;
  if (!plan || plan === 'free') return false;
  return plan === ADDONS.allTiersPlan || (ADDONS.plans[plan] || []).includes(tier);
}
function featureUnlocked(name) {
  if (CORE_FEATURES[name]) return true;
  const f = ADDONS.features[name];
  return !!f && state.unlocked && tierUnlocked(f.tier, currentPlan());
}
function featuresStatus() {
  const out = {};
  for (const [n, label] of Object.entries(CORE_FEATURES)) out[n] = { available: true, unlocked: true, tier: 'community', label };
  for (const [n, f] of Object.entries(ADDONS.features)) out[n] = { available: true, unlocked: featureUnlocked(n), tier: f.tier, label: f.label };
  return out;
}
function uiStatus() {
  const feats = featuresStatus();
  const state1 = e => (!e.feature ? 'on' : !feats[e.feature] ? null : feats[e.feature].unlocked ? 'on' : 'locked');
  const out = {};
  for (const k of ['screens', 'cards', 'settings']) {
    out[k] = (ADDONS.ui[k] || []).map(e => ({ ...e, state: state1(e),
      title: e.title || (k === 'cards' && e.feature && feats[e.feature] ? feats[e.feature].label : e.title) })).filter(e => e.state);
  }
  return out;
}
function vaultOut(v) {
  const unlocked = state.unlocked && v.real === state.vaultName;
  return { name: unlocked ? v.real : (v.label || ''), label: v.label || '', label_hidden: !v.label, created: v.created, path: v.path, slug: v.path.split('/').pop().replace('.vault', ''), unlocked, recovery: !!v.recovery };
}

function statusPayload() {
  const u = state.unlocked;
  const plan = currentPlan();
  const v = state.vaults.find(x => x.real === state.vaultName);
  const aiOn = u && state.settings.ai_enabled && state.settings.provider !== 'off';
  return {
    version: '1.0.0-mock', edition: EDITION, unlocked: u, vault_name: u ? state.vaultName : null, vault_path: u && v ? v.path : null,
    plan: u ? plan : null, limits: null,
    license: u ? { plan, email: state.license.email, expires: state.license.expires, valid: !!state.license.valid } : null,
    features: featuresStatus(), source: SOURCE, dev_override: DEV,
    plans: { free: 'Free', community: 'Community', pro: 'Pro', enterprise: 'Enterprise', ...ADDONS.planLabels },
    ui: uiStatus(), ai_providers: ADDONS.aiProviders || [],
    signup_url: SIGNUP_URL, recommended_local_model: RECOMMENDED_LOCAL_MODEL,
    ollama_operator_url: qs.get('server') === '1' ? 'http://ollama:11434' : null,
    ai: aiOn ? { enabled: true, provider: state.settings.provider, model: state.settings.model } : { enabled: false, provider: null, model: null },
    restart_required: qs.get('restart') === '1',
    counts: u ? { accounts: state.accounts.length, transactions: state.txns.length } : null,
    auto_lock_minutes: 30,
    server_mode: qs.get('server') === '1',
    onboarding: onboardingOut(),
    ...(u ? { review_count: state.txns.filter(t => t.status === 'review' && !t.duplicate_of).length, tax_year: state.settings.tax_year || YEAR,
      recovery: recoveryOut(), backup: backupOut() } : {}),
  };
}

// ---------------------------------------------------------------- add-ons (their own mock.js)
/** What an add-on's mockRoutes(fixtures) gets: the demo state and the helpers the core uses itself. */
function addonFixtures() {
  return {
    state, YEAR, CATEGORIES, CAT, SC_NAMES, EMPTY,
    json, err, sleep, bodyOf, filesOf, applyFields, out, summarize, rematch, rcOut,
    rnd, between, money, pick, randDate, num,
    plan: currentPlan, unlocked: featureUnlocked, featuresStatus, statusPayload,
    /** 402 body for a locked add-on feature, the same shape the real API sends. */
    locked: name => err(402, `${(ADDONS.features[name] || {}).label || name} needs a license that includes it.`, { feature: name, upgrade: true, installed: true }),
    /** Wrap a handler: 402 while `feature` is locked. */
    gate: (feature, fn) => req => (featureUnlocked(feature) ? fn(req) : err(402, `${(ADDONS.features[feature] || {}).label || feature} needs a license that includes it.`, { feature, upgrade: true, installed: true })),
    fileResponse: (body, type, name) => new Response(body instanceof Blob ? body : new Blob([body], { type }),
      { status: 200, headers: { 'content-type': type, 'content-disposition': `attachment; filename="${name}"` } }),
  };
}

async function loadAddons() {
  if (ADDONS.loaded) return ADDONS.loaded;
  ADDONS.loaded = (async () => {
    let idx = null;
    try {
      const r = await fetch('/addons/index.json', { credentials: 'same-origin', cache: 'no-store' });
      if (r.ok && (r.headers.get('content-type') || '').includes('json')) idx = await r.json();
    } catch { idx = null; }      // a static demo without a server: no add-ons
    if (!idx || !Array.isArray(idx.addons)) return;
    ADDONS.list = idx.addons;
    ADDONS.features = idx.features || {};
    ADDONS.plans = idx.plans || ADDONS.plans;
    ADDONS.planLabels = idx.plan_labels || {};
    ADDONS.allTiersPlan = idx.all_tiers_plan || 'enterprise';
    ADDONS.ui = idx.ui || ADDONS.ui;
    if (ADDONS.list.length) EDITION = 'pro';
    const fx = addonFixtures();
    for (const a of ADDONS.list) {
      if (!a.mock) continue;
      try {
        const m = await import(a.mock);
        if (typeof m.mockRoutes !== 'function') continue;
        const routes = await m.mockRoutes(fx);
        for (const rt of routes || []) if (rt && rt.path && typeof rt.handler === 'function') ADDONS.routes.push({ ns: a.ns, ...rt, method: (rt.method || 'GET').toUpperCase() });
        if (typeof m.mockSettings === 'function') Object.assign(state.settings, m.mockSettings(fx) || {});
        if (Array.isArray(m.aiProviders)) ADDONS.aiProviders = [...(ADDONS.aiProviders || []), ...m.aiProviders];
      } catch { /* a broken add-on mock must not break the demo */ }
    }
  })();
  return ADDONS.loaded;
}

async function addonRoute(path, method, url, init) {
  for (const rt of ADDONS.routes) {
    if (rt.method !== method) continue;
    const match = typeof rt.path === 'string' ? (rt.path === path ? [path] : null) : path.match(rt.path);
    if (!match) continue;
    const form = init && typeof FormData !== 'undefined' && init.body instanceof FormData ? init.body : null;
    const res = await rt.handler({ method, path, url, query: url.searchParams, body: form ? {} : bodyOf(init), form, match, init });
    if (res === undefined) continue;
    return res instanceof Response ? res : json(res);
  }
  return undefined;
}

// ---------------------------------------------------------------- router
export async function mockFetch(input, init = {}) {
  const url = new URL(typeof input === 'string' ? input : input.url, location.origin);
  const method = (init.method || 'GET').toUpperCase();
  const path = url.pathname;
  const q = url.searchParams;
  await sleep(120 + Math.random() * 260);
  await loadAddons();

  if (!path.startsWith('/api/')) return err(404, 'Not found');
  const open = ['/api/status', '/api/vaults', '/api/unlock', '/api/unlock/recovery', '/api/vaults/restore'];
  if (path.startsWith('/api/setup/')) {
    return setupRoutes(path, method, init, { json, err, bodyOf, loadDemo, onboarding: onboardingOut,
      setOnboarding: done => { state.onboarding.completed = done; },
      setLocalModel: m => { state.onboarding.local_ai_model = m; state.onboarding.local_ai_requested = null; if (!state.settings.ai_enabled) Object.assign(state.settings, { ai_enabled: true, provider: 'ollama', model: m }); },
      setLocalRequested: m => { state.onboarding.local_ai_requested = m; } });
  }
  if (path === '/api/source') return json({ ...SOURCE, version: '1.0.0-mock', edition: EDITION, note: 'LedgerStitch Community is free software under the GNU AGPL v3. Add-ons, if installed, are licensed separately.' });
  if (!state.unlocked && !open.includes(path)) return err(409, 'The vault is locked. Unlock it to continue.', { locked: true });
  const fromAddon = await addonRoute(path, method, url, init);
  if (fromAddon !== undefined) return fromAddon;
  const taxDoc = taxDocRoutes(path, method, init, { json, err });
  if (taxDoc !== undefined) return taxDoc;

  // status & vaults
  if (path === '/api/status') return json(statusPayload());
  if (path === '/api/vaults' && method === 'GET') return json(state.vaults.map(vaultOut));
  if (path === '/api/vaults' && method === 'POST') {
    const b = bodyOf(init);
    if (!b.name || !b.passphrase) return err(422, 'Please give the workspace a name and a passphrase.');
    const slug = b.name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
    void slug;
    const code = b.recovery ? mockRecoveryCode() : null;
    state.vaults.push({ real: b.name, label: (b.label || '').trim(), created: new Date().toISOString(), path: `~/LedgerStitch/ws-${Math.random().toString(16).slice(2, 10)}.vault`,
      recovery: code ? new Date().toISOString().slice(0, 10) : null });
    state.unlocked = true; state.vaultName = b.name;
    state.txns = []; state.accounts = []; state.rules = []; state.lastBackupAt = null;
    state.settings.business_name = b.name;
    return json({ ...statusPayload(), ...(code ? { recovery_code: code } : {}) }, 201);
  }
  if (path === '/api/unlock') {
    const b = bodyOf(init);
    if (!b.passphrase || b.passphrase.toLowerCase() === 'wrong') return err(401, 'Wrong passphrase.');
    const v = state.vaults.find(x => x.path === b.path) || state.vaults[0];
    state.unlocked = true; state.vaultName = v.real;
    return json(statusPayload());
  }
  if (path === '/api/unlock/recovery' && method === 'POST') {
    const b = bodyOf(init);
    const v = state.vaults.find(x => x.path === b.path) || state.vaults[0];
    if (!v.recovery) return err(422, 'This workspace has no recovery key.');
    const raw = String(b.recovery_code || '').replace(/[\s-]+/g, '').toUpperCase();
    if (raw.length !== 32) return err(422, 'a recovery code has 32 letters and digits (2-7), in groups of four');
    if (raw.startsWith('WRONG')) return err(401, "That recovery code didn't unlock this workspace.");
    if (!b.new_passphrase || b.new_passphrase.length < 8) return err(422, 'New passphrase must be at least 8 characters.');
    state.unlocked = true; state.vaultName = v.real;
    return json(statusPayload());
  }
  if (path === '/api/vaults/restore' && method === 'POST') {
    const [f] = filesOf(init.body);
    if (!f) return err(422, 'Attach a .lsbackup file.');
    if (!/\.lsbackup$/i.test(f.name)) return err(422, "That backup can't be restored: this is not a LedgerStitch backup file.");
    const v = { real: 'Restored workspace', label: '', created: new Date().toISOString(), path: `~/LedgerStitch/ws-${Math.random().toString(16).slice(2, 10)}.vault`, recovery: '2025-01-06' };
    state.vaults.push(v);
    return json({ restored: vaultOut(v), vaults: state.vaults.map(vaultOut) }, 201);
  }
  if (path === '/api/lock') { state.unlocked = false; return json(statusPayload()); }
  if (path === '/api/recovery' && method === 'POST') {
    const b = bodyOf(init);
    if (!b.passphrase || b.passphrase.toLowerCase() === 'wrong') return err(401, 'Wrong passphrase.');
    const v = currentVault();
    if (v) v.recovery = new Date().toISOString().slice(0, 10);
    return json({ recovery_code: mockRecoveryCode(), ...recoveryOut() });
  }
  if (path === '/api/recovery/remove' && method === 'POST') {
    const b = bodyOf(init);
    if (!b.passphrase || b.passphrase.toLowerCase() === 'wrong') return err(401, 'Wrong passphrase.');
    const v = currentVault();
    if (v) v.recovery = null;
    return json(recoveryOut());
  }
  if (path === '/api/backup' && method === 'GET') {
    // Stand-in bytes: a real backup is a zip of the already-encrypted vault files.
    const body = crypto.getRandomValues(new Uint8Array(4096));
    state.lastBackupAt = new Date().toISOString().slice(0, 19) + '+00:00';
    const name = `${(state.vaultName || 'workspace').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')}-demo.lsbackup`;
    return new Response(new Blob([body], { type: 'application/octet-stream' }), { status: 200, headers: { 'content-type': 'application/octet-stream',
      'content-disposition': `attachment; filename="${name}"` } });
  }
  if (path === '/api/passphrase') {
    const b = bodyOf(init);
    if (!b.old || b.old.toLowerCase() === 'wrong') return err(401, 'Current passphrase is wrong.');
    if (!b.new || b.new.length < 8) return err(422, 'New passphrase must be at least 8 characters.');
    return json({ ok: true });
  }

  // imports
  let m0;
  if (path === '/api/imports' && method === 'POST') {
    const files = filesOf(init.body);
    if (!files.length) return err(422, 'No file was attached.');
    const label = init.body.get && init.body.get('account_label');
    const out = [];
    for (const f of files) {
      const isPdf = f.name.toLowerCase().endsWith('.pdf');
      const text = isPdf ? '' : await f.text();
      const rows = isPdf ? [] : parseCSV(text, 2000);
      const det = detectFrom(f.name, rows[0] || []);
      if (!isPdf && !/\.(csv|txt|tsv)$/i.test(f.name)) return err(415, `${f.name} isn't a CSV or PDF statement.`);
      if (det.confidence < 0.6) {
        out.push({ filename: f.name, error: `${f.name}: we don't recognise this CSV layout yet. Map the columns to import it.`, status: 422, needs_mapping: true });
        continue;
      }
      const acct = ensureAccount(det, label);
      if (!acct) return err(500, 'Could not create the account.');
      const importId = 'imp-' + (++state.importSeq);
      const count = isPdf ? 46 : Math.max(rows.length - 1, 0);
      addImportedRows(acct.id, Math.min(count, 60), importId);
      const warnings = [];
      if (isPdf) warnings.push('2 lines on page 3 looked like transactions but had no amount — skipped. Check them against the statement.');
      if (count > 20) warnings.push(`${Math.floor(count / 25)} possible duplicates of earlier imports were set aside.`);
      const reconciled = /mismatch/i.test(f.name) ? false : isPdf ? true : null;
      // amount the parsed rows don't account for (null when the file has nothing to check against)
      const reconcile_difference = reconciled === false ? '50.00' : reconciled === true ? '0.00' : null;
      if (reconciled === false) warnings.push('reconciliation: parsed money-in 1650.00 differs from statement total 1700.00');
      state.imports.unshift({ import_id: importId, filename: f.name, institution: det.institution, account_id: acct.id, count, new: count, warnings, reconciled, reconcile_difference });
      out.push({ import_id: importId, filename: f.name, detected: { profile: det.id, ...det, mapping: null, account_id: acct.id, account_label: acct.label }, account_id: acct.id, count, new: count, warnings, reconciled, reconcile_difference, reconcile: {}, categorize: { warnings: [] } });
    }
    const fails = out.filter(r => r.error);
    if (fails.length && fails.length === out.length) return err(fails[0].status, fails[0].error, { results: out, needs_mapping: fails[0].needs_mapping, upgrade: false });
    return json(out);
  }
  if (path === '/api/imports' && method === 'GET') return json(state.imports.map(i => ({ ...i,
    account_label: (state.accounts.find(a => a.id === i.account_id) || {}).label || null,
    transactions: state.txns.filter(t => t.import_id === i.import_id).length })));
  if ((m0 = path.match(/^\/api\/imports\/([^/]+)$/)) && method === 'DELETE') {
    const id = decodeURIComponent(m0[1]);
    const rec = state.imports.find(i => i.import_id === id);
    if (!rec) return err(404, 'Import not found.');
    const before = state.txns.length;
    state.txns = state.txns.filter(t => t.import_id !== id);
    state.imports = state.imports.filter(i => i.import_id !== id);
    const keep = state.accounts.length;
    state.accounts = state.accounts.filter(a => a.id !== rec.account_id || state.txns.some(t => t.account_id === a.id) || state.imports.some(i => i.account_id === a.id));
    return json({ deleted_transactions: before - state.txns.length, deleted_accounts: keep - state.accounts.length });
  }
  // your data: purge, export, delete workspace
  if (path === '/api/data/purge' && method === 'POST') {
    const b = bodyOf(init);
    if ((b.confirm || '').trim() !== 'DELETE') return err(422, 'Type DELETE to confirm.');
    if (!['transactions', 'all'].includes(b.scope)) return err(422, "scope must be 'transactions' or 'all'.");
    const deleted = { transactions: state.txns.length, imports: state.imports.length, accounts: state.accounts.length, receipts: state.receipts.length };
    state.txns = []; state.imports = []; state.accounts = []; state.receipts = [];
    if (b.scope === 'all') { deleted.rules = state.rules.length; state.rules = []; state.settings.business_profile = ''; }
    return json({ scope: b.scope, deleted, addon_errors: [] });
  }
  if (path === '/api/data/export' && method === 'GET') {
    const body = JSON.stringify({ format: 'ledgerstitch-export', version: 1, demo: true, accounts: state.accounts, imports: state.imports, transactions: state.txns, rules: state.rules }, null, 2);
    return new Response(new Blob([body], { type: 'application/json' }), { status: 200, headers: { 'content-type': 'application/json',
      'content-disposition': `attachment; filename="${(state.vaultName || 'workspace').replace(/[^A-Za-z0-9._-]+/g, '-')}-ledgerstitch-data-demo.json"` } });
  }
  if (path === '/api/vaults/delete' && method === 'POST') {
    const b = bodyOf(init);
    if (!b.passphrase || b.passphrase.toLowerCase() === 'wrong') return err(401, 'Wrong passphrase.');
    if ((b.confirm || '').trim() !== 'DELETE') return err(422, 'Type DELETE to confirm.');
    state.vaults = state.vaults.filter(v => v.real !== state.vaultName);
    state.unlocked = false; state.txns = []; state.accounts = []; state.imports = []; state.rules = [];
    return json({ ...statusPayload(), deleted: { files: 12, addon_errors: [] } });
  }
  if (path === '/api/imports/preview') {
    const [f] = filesOf(init.body);
    if (!f) return err(422, 'No file was attached.');
    if (f.name.toLowerCase().endsWith('.pdf')) return err(422, "PDF statements don't need column mapping.");
    const rows = parseCSV(await f.text(), 9);
    if (!rows.length) return err(422, 'That file looks empty.');
    return json({ columns: rows[0], rows: rows.slice(1), detected: detectFrom(f.name, rows[0]) });
  }
  if (path === '/api/imports/mapped') {
    const [f] = filesOf(init.body);
    if (!f) return err(422, 'No file was attached.');
    let mapping = {};
    try { mapping = JSON.parse(init.body.get('mapping') || '{}'); } catch { return err(422, 'The column mapping was not valid.'); }
    if (!mapping.date || !mapping.description || !(mapping.amount || mapping.debit || mapping.credit)) return err(422, 'Pick the date, description and amount columns.');
    const label = init.body.get('account_label') || 'Imported account';
    const rows = parseCSV(await f.text(), 5000);
    const det = { id: 'manual', institution: label.split(' ')[0] || 'Manual', kind: 'other', confidence: 1, account_hint: null };
    const acct = ensureAccount(det, label);
    if (!acct) return err(500, 'Could not create the account.');
    const importId = 'imp-' + (++state.importSeq);
    const count = Math.max(rows.length - 1, 0);
    addImportedRows(acct.id, Math.min(count, 60), importId);
    state.imports.unshift({ import_id: importId, filename: f.name, institution: det.institution, account_id: acct.id, count, new: count, warnings: [], reconciled: null, reconcile_difference: null });
    return json({ import_id: importId, filename: f.name, detected: { ...det, account_id: acct.id, account_label: acct.label }, account_id: acct.id, count, new: count, warnings: [], reconciled: null, reconcile_difference: null, reconcile: {}, categorize: { warnings: [] } });
  }

  // accounts
  if ((m0 = path.match(/^\/api\/accounts\/([^/]+)$/)) && method === 'DELETE') {
    const id = decodeURIComponent(m0[1]);
    if (!state.accounts.some(a => a.id === id)) return err(404, `No account '${id}'.`);
    const before = state.txns.length, imps = state.imports.length;
    state.txns = state.txns.filter(t => t.account_id !== id);
    state.imports = state.imports.filter(i => i.account_id !== id);
    state.accounts = state.accounts.filter(a => a.id !== id);
    return json({ deleted_transactions: before - state.txns.length, deleted_imports: imps - state.imports.length });
  }
  if (path === '/api/accounts') return json(state.accounts.map(a => ({ ...a, transactions: state.txns.filter(t => t.account_id === a.id).length })));
  let m;
  if ((m = path.match(/^\/api\/accounts\/([^/]+)$/)) && method === 'PATCH') {
    const a = state.accounts.find(x => x.id === decodeURIComponent(m[1]));
    if (!a) return err(404, 'Account not found.');
    Object.assign(a, bodyOf(init));
    return json(a);
  }

  // transactions
  if (path === '/api/transactions') {
    let rows = state.txns.slice();
    const f = k => q.get(k);
    if (f('include_duplicates') === 'false') rows = rows.filter(t => !t.duplicate_of);
    if (f('lane')) rows = rows.filter(t => t.lane === f('lane'));
    if (f('category')) rows = rows.filter(t => t.category === f('category'));
    if (f('account')) rows = rows.filter(t => t.account_id === f('account'));
    if (f('status')) rows = rows.filter(t => t.status === f('status'));
    if (f('year')) rows = rows.filter(t => t.date.startsWith(f('year')));
    if (f('date_from')) rows = rows.filter(t => t.date >= f('date_from'));
    if (f('date_to')) rows = rows.filter(t => t.date <= f('date_to'));
    if (f('direction')) rows = rows.filter(t => t.direction === f('direction'));
    if (f('q')) { const s = f('q').toLowerCase(); rows = rows.filter(t => (t.description + ' ' + t.merchant + ' ' + (t.note || '') + ' ' + (t.category || '')).toLowerCase().includes(s)); }
    const sort = f('sort') || 'date', dir = f('order') === 'asc' ? 1 : -1;
    rows.sort((a, b) => {
      let x = a[sort], y = b[sort];
      if (sort === 'amount') { x = a.direction === 'out' ? -num(a.amount) : num(a.amount); y = b.direction === 'out' ? -num(b.amount) : num(b.amount); }
      x = x ?? ''; y = y ?? '';
      return (x < y ? -1 : x > y ? 1 : 0) * dir;
    });
    const total = rows.length;
    const offset = +f('offset') || 0, limit = +f('limit') || 100;
    return json({ total, limit, offset, items: rows.slice(offset, offset + limit).map(out) });
  }
  if (path === '/api/transactions/delete' && method === 'POST') {
    const ids = new Set(bodyOf(init).ids || []);
    const before = state.txns.length;
    state.txns = state.txns.filter(t => !ids.has(t.id));
    return json({ deleted_transactions: before - state.txns.length });
  }
  if (path === '/api/transactions/bulk' && method === 'POST') {
    const b = bodyOf(init);
    const ids = new Set(b.ids || []);
    let n = 0;
    for (const t of state.txns) if (ids.has(t.id)) { applyFields(t, b); n++; }
    return json({ updated: n });
  }
  if ((m = path.match(/^\/api\/transactions\/([^/]+)$/)) && method === 'PATCH') {
    const t = state.txns.find(x => x.id === decodeURIComponent(m[1]));
    if (!t) return err(404, 'Transaction not found.');
    const b = bodyOf(init);
    applyFields(t, b);
    let also = 0;
    const out0 = () => out(t);
    if (b.remember) {
      addRuleFrom(t);
      for (const o of state.txns) if (o !== t && o.merchant === t.merchant && o.status !== 'approved') { applyFields(o, { category: t.category, lane: t.lane, schedule_c: t.schedule_c, status: o.status === 'review' ? 'auto' : o.status }); o.source = 'rule'; also++; }
    }
    return json({ transaction: out0(), changed: also });
  }

  // categories & rules
  if (path === '/api/categories' && method === 'GET') return json(CATEGORIES);
  if (path === '/api/categories' && method === 'POST') {
    const b = bodyOf(init);
    if (!b.name) return err(422, 'Give the category a name.');
    if (CAT[b.name]) return err(409, 'That category already exists.');
    const c = { name: b.name, lane: b.lane || 'business', schedule_c: b.schedule_c || null };
    CATEGORIES.push(c); CAT[c.name] = c;
    return json(c);
  }
  if (path === '/api/rules' && method === 'GET') return json(state.rules);
  if (path === '/api/rules' && method === 'POST') {
    const b = bodyOf(init);
    const r = { id: 'r-' + Math.random().toString(36).slice(2, 10), field: 'merchant', account_id: null, created_by: 'user', ...b };
    delete r.apply;
    state.rules.unshift(r);
    return json({ rule: r, changed: 0 }, 201);
  }
  if ((m = path.match(/^\/api\/rules\/([^/]+)$/)) && method === 'DELETE') {
    state.rules = state.rules.filter(r => r.id !== decodeURIComponent(m[1]));
    return json({ ok: true });
  }

  // AI
  if (path === '/api/categorize') {
    await sleep(900);
    const pending = state.txns.filter(t => t.status === 'review');
    const merchants = [...new Set(pending.map(t => t.merchant))];
    const aiOn = state.settings.ai_enabled && state.settings.provider === 'ollama';
    return json({ by_rules: 3, by_heuristics: 11, by_ai: aiOn ? 7 : 0, needs_review: pending.length, new_categories: [],
      sent_descriptors: aiOn ? merchants.length : 0, warnings: [], new_category_defs: {}, report: null });
  }
  if (path === '/api/ai/preview') {
    const merchants = [...new Set(state.txns.filter(t => t.status === 'review').map(t => t.merchant))].sort();
    const payload = {
      task: 'Categorize each merchant descriptor for a small-business expense report.',
      business_profile: state.settings.business_profile || '',
      categories: CATEGORIES.map(c => ({ name: c.name, lane: c.lane, schedule_c: c.schedule_c })),
      descriptors: merchants,
    };
    const full_prompt = 'SYSTEM:\nYou categorize merchant descriptors from bank statements for a small-business expense report. ' +
      'You only ever see redacted merchant names. Reply with JSON: [{descriptor, category, lane, schedule_c, confidence}].\n\n' +
      'USER:\n' + JSON.stringify(payload, null, 2);
    const local = state.settings.provider === 'ollama';
    const out = { descriptors: merchants, payload: JSON.stringify(payload, null, 2), count: merchants.length,
      enabled: local && !!state.settings.ai_enabled, provider: local ? 'ollama' : null, available: false, unlocked: false };
    if (local) { out.local = true; out.full_prompt = full_prompt; }
    return json(out);
  }
  if (path === '/api/ai/local-test' && method === 'POST') {
    await sleep(1100);
    const model = state.settings.model || 'llama3.1';
    const url = state.settings.ollama_url || 'http://127.0.0.1:11434';
    const sent = ['BLUEJAY PRINT CO', 'METRO PARKING 114', 'SQ *LUNA TACOS'];
    if (/missing|nope/i.test(model)) return json({ ok: false, error: `Ollama on this computer doesn't have the model "${model}". Run: ollama pull ${model}`, sent, results: [] });
    return json({ ok: true, provider: 'ollama', model, url, seconds: 2.4, sent, warnings: [],
      results: [{ descriptor: sent[0], category: 'Advertising & Marketing', lane: 'business', confidence: 0.71 },
        { descriptor: sent[1], category: 'Parking & Tolls', lane: 'business', confidence: 0.66 },
        { descriptor: sent[2], category: 'Dining & Restaurants', lane: 'personal', confidence: 0.62 }] });
  }
  // receipts
  if (path === '/api/receipts' && method === 'GET') return json(state.receipts.map(rcOut));
  if ((m = path.match(/^\/api\/receipts\/([^/]+)\/image$/))) {
    const r = state.receipts.find(x => x.id === decodeURIComponent(m[1]));
    if (!r) return err(404, 'Receipt not found.');
    if (r._blob) return new Response(r._blob, { status: 200, headers: { 'content-type': r._blob.type || r.mime } });
    if (r.mime === 'application/pdf') return new Response(new Blob(['%PDF-1.4\n% synthetic demo receipt\n'], { type: 'application/pdf' }), { status: 200, headers: { 'content-type': 'application/pdf' } });
    return new Response(new Blob([r._svg || TINY_RECEIPT('RECEIPT', [], '$0.00')], { type: 'image/svg+xml' }), { status: 200, headers: { 'content-type': 'image/svg+xml' } });
  }
  if ((m = path.match(/^\/api\/receipts\/([^/]+)\/match$/)) && method === 'POST') {
    const r = state.receipts.find(x => x.id === decodeURIComponent(m[1]));
    if (!r) return err(404, 'Receipt not found.');
    const b = bodyOf(init);
    for (const t of state.txns) if (t.receipt_id === r.id) t.receipt_id = null;
    if (b.txn_id) {
      const t = state.txns.find(x => x.id === b.txn_id);
      if (!t) return err(404, 'Transaction not found.');
      t.receipt_id = r.id;
      const c = (r.candidates || []).find(x => x.txn_id === t.id);
      Object.assign(r, { txn_id: t.id, status: 'manual', match_score: c ? c.score : null, match_reasons: c ? c.reasons : ['Linked by you'] });
    } else { r.status = 'unmatched'; r.txn_id = null; rematch(r); if (r.status === 'matched') { r.status = 'suggested'; for (const t of state.txns) if (t.receipt_id === r.id) t.receipt_id = null; r.txn_id = null; } }
    return json(rcOut(r));
  }
  if ((m = path.match(/^\/api\/receipts\/([^/]+)$/)) && method === 'PATCH') {
    const r = state.receipts.find(x => x.id === decodeURIComponent(m[1]));
    if (!r) return err(404, 'Receipt not found.');
    const b = bodyOf(init);
    for (const k of ['merchant', 'date', 'total', 'card_last4']) if (b[k] !== undefined) r[k] = b[k];
    if (r.status === 'manual' || r.status === 'no_text') r.status = 'unmatched';
    rematch(r);
    return json(rcOut(r));
  }
  if ((m = path.match(/^\/api\/receipts\/([^/]+)$/)) && method === 'DELETE') {
    const id = decodeURIComponent(m[1]);
    state.receipts = state.receipts.filter(x => x.id !== id);
    for (const t of state.txns) if (t.receipt_id === id) t.receipt_id = null;
    return json({ ok: true });
  }

  // settings & license
  if (path === '/api/settings' && method === 'GET') return json(state.settings);
  if (path === '/api/settings' && method === 'PUT') {
    const b = bodyOf(init);
    if (b.ollama_url !== undefined && !/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?\/?$/i.test(String(b.ollama_url).trim()))
      return err(422, 'Local AI only talks to Ollama on this computer (127.0.0.1, localhost or ::1).');
    for (const k of Object.keys(b)) {
      if (k.endsWith('_api_key')) { state.settings[`has_${k}`] = !!b[k]; delete b[k]; }
      else if (k.startsWith('has_')) delete b[k];
    }
    if (b.tax_year !== undefined) b.tax_year = b.tax_year ? +b.tax_year : null;
    Object.assign(state.settings, b);
    return json(state.settings);
  }
  if (path === '/api/license' && method === 'POST') {
    const b = bodyOf(init);
    const key = String(b.key || '').trim();
    if (key && !/^(LL[-_]|LS1\.)/i.test(key)) return err(422, 'That license key is not valid (or has expired).');
    if (!key) { state.license = { email: null, plan: 'free', expires: null, valid: false }; state.settings.has_license_key = false; return json(statusPayload()); }
    // demo keys: "LS1.<plan>…" picks that plan if an add-on declared it; anything else is "pro"
    const named = (key.match(/^LS1\.([a-z][a-z0-9_]*)/i) || [])[1];
    const plan = named && (ADDONS.plans[named.toLowerCase()] || named.toLowerCase() === 'enterprise') ? named.toLowerCase() : /enterprise/i.test(key) ? 'enterprise' : 'pro';
    state.license = { email: 'you@example.com', plan, expires: '2026-12-31', valid: true };
    state.settings.has_license_key = true;
    return json(statusPayload());
  }

  // summary & exports
  if (path === '/api/summary') return json(+q.get('year') === YEAR - 1 && !EMPTY ? priorYearSummary() : summarize(q.get('year')));
  if ((m = path.match(/^\/api\/export\/(xlsx|csv|qbo|iif)$/))) {
    const kind = m[1], year = q.get('year') || YEAR;
    const rows = state.txns.filter(t => !t.duplicate_of && t.date.startsWith(String(year)));
    const label = id => (state.accounts.find(a => a.id === id) || {}).label || id;
    let body, type, ext = kind;
    if (kind === 'iif') {
      body = '!TRNS\tDATE\tACCNT\tNAME\tAMOUNT\tMEMO\n' + rows.map(t => `TRNS\t${t.date}\t${label(t.account_id)}\t${t.merchant}\t${t.direction === 'out' ? '-' : ''}${t.amount}\t${t.category}`).join('\n');
      type = 'application/octet-stream';
    } else {
      const head = kind === 'qbo' ? 'Date,Description,Amount' : 'Date,Account,Description,Merchant,Amount,Lane,Category,Schedule C,Status,Note';
      body = head + '\n' + rows.map(t => {
        const amt = (t.direction === 'out' ? '-' : '') + t.amount;
        const c = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
        return kind === 'qbo' ? [t.date, c(t.description), amt].join(',') : [t.date, c(label(t.account_id)), c(t.description), c(t.merchant), amt, t.lane, c(t.category), t.schedule_c || '', t.status, c(t.note)].join(',');
      }).join('\n');
      type = 'text/csv';
      if (kind === 'xlsx') ext = 'csv'; // mock can't build a real workbook; hands back CSV
      if (kind === 'qbo') ext = 'csv';
    }
    const name = `ledgerstitch-${kind === 'qbo' ? 'quickbooks-online' : kind === 'iif' ? 'quickbooks-desktop' : 'report'}-${year}${kind === 'xlsx' ? '-demo' : ''}.${ext}`;
    return new Response(new Blob([body], { type }), { status: 200, headers: { 'content-type': type, 'content-disposition': `attachment; filename="${name}"` } });
  }

  return err(404, `Mock has no handler for ${method} ${path}`);
}
