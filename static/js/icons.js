/**
 * SVG icon registry
 * All icons are inline SVGs, no external dependencies
 */

export const ICONS = {
    // ── Brand ──────────────────────────────────────────────────────────────────
    flame: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <path d="M128 24c0 0-60 72-60 120a60 60 0 0 0 120 0c0-48-60-120-60-120z" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M128 204a32 32 0 0 0 32-32c0-24-32-56-32-56s-32 32-32 56a32 32 0 0 0 32 32z" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`,

    // ── Navigation ─────────────────────────────────────────────────────────────
    back: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="20">
        <polyline points="160,40 64,128 160,216" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`,

    close: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="20">
        <line x1="64" y1="64" x2="192" y2="192"/>
        <line x1="192" y1="64" x2="64" y2="192"/>
    </svg>`,

    // ── Actions ────────────────────────────────────────────────────────────────
    edit: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <path d="M180 40l36 36-128 128H52v-36L180 40z" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`,

    delete: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <path d="M48 72h160M104 72V48h48v24M56 72l16 136h112l16-136" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`,

    key: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <rect x="48" y="120" width="160" height="112" rx="8"/>
        <path d="M88 120V80a40 40 0 0 1 80 0v40" stroke-linecap="round"/>
        <circle cx="128" cy="176" r="16"/>
    </svg>`,

    plus: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <line x1="128" y1="48" x2="128" y2="208"/>
        <line x1="48" y1="128" x2="208" y2="128"/>
    </svg>`,

    refresh: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <path d="M176 160a48 48 0 0 1-96 0" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="128,112 128,160 176,160" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`,

    // ── User ───────────────────────────────────────────────────────────────────
    user: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <circle cx="128" cy="96" r="64"/>
        <path d="M32 216c0-52.94 43.06-96 96-96s96 43.06 96 96" stroke-linecap="round"/>
    </svg>`,

    users: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <circle cx="96" cy="112" r="48"/>
        <circle cx="176" cy="112" r="48"/>
        <path d="M32 216c0-40 32-72 72-72s72 32 72 72" stroke-linecap="round"/>
        <path d="M176 160c8 0 24 16 48 16" stroke-linecap="round"/>
    </svg>`,

    // ── Communication ──────────────────────────────────────────────────────────
    bell: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <path d="M96 192a32 32 0 0 0 64 0" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M56 96a72 72 0 0 1 144 0c0 35.86 8 58.37 16 72H40c8-13.63 16-36.14 16-72z" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`,

    clock: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <circle cx="128" cy="128" r="96"/>
        <polyline points="128,72 128,128 168,152" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`,

    check: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <circle cx="128" cy="128" r="96"/>
        <polyline points="88,128 112,152 168,96" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`,

    x: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <circle cx="128" cy="128" r="96"/>
        <line x1="96" y1="96" x2="160" y2="160"/>
        <line x1="160" y1="96" x2="96" y2="160"/>
    </svg>`,

    mail: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <rect x="32" y="64" width="192" height="128" rx="8"/>
        <polyline points="32,64 128,144 224,64" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`,

    // ── Documents ──────────────────────────────────────────────────────────────
    document: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <path d="M40 216V48a8 8 0 0 1 8-8h160a8 8 0 0 1 8 8v168l-32-16-32 16-32-16-32 16-32-16-32 16z" stroke-linecap="round" stroke-linejoin="round"/>
        <line x1="80" y1="80" x2="176" y2="80"/>
        <line x1="80" y1="112" x2="176" y2="112"/>
        <line x1="80" y1="144" x2="128" y2="144"/>
    </svg>`,

    list: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <line x1="88" y1="64" x2="216" y2="64"/>
        <line x1="88" y1="128" x2="216" y2="128"/>
        <line x1="88" y1="192" x2="216" y2="192"/>
        <circle cx="56" cy="64" r="8" fill="currentColor"/>
        <circle cx="56" cy="128" r="8" fill="currentColor"/>
        <circle cx="56" cy="192" r="8" fill="currentColor"/>
    </svg>`,

    attachment: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <path d="M216 112v80a24 24 0 0 1-24 24H64a24 24 0 0 1-24-24v-80" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="176,48 128,96 80,48" stroke-linecap="round" stroke-linejoin="round"/>
        <line x1="128" y1="96" x2="128" y2="184"/>
    </svg>`,

    // ── Media ──────────────────────────────────────────────────────────────────
    send: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <path d="M48 192l160-64L48 64v52l100 12-100 12z" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`,

    upload: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <path d="M216 112v80a24 24 0 0 1-24 24H64a24 24 0 0 1-24-24v-80" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="176,48 128,96 80,48" stroke-linecap="round" stroke-linejoin="round"/>
        <line x1="128" y1="96" x2="128" y2="184"/>
    </svg>`,

    download: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <path d="M216 112v80a24 24 0 0 1-24 24H64a24 24 0 0 1-24-24v-80" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="80,144 128,192 176,144" stroke-linecap="round" stroke-linejoin="round"/>
        <line x1="128" y1="48" x2="128" y2="192"/>
    </svg>`,

    // ── Interface ──────────────────────────────────────────────────────────────
    search: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <circle cx="112" cy="112" r="64"/>
        <line x1="160" y1="160" x2="224" y2="224" stroke-linecap="round"/>
    </svg>`,

    settings: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <circle cx="128" cy="128" r="32"/>
        <path d="M128 24v32M128 200v32M24 128h32M200 128h32M45.42 45.42l22.63 22.63M187.95 187.95l22.63 22.63M45.42 210.58l22.63-22.63M187.95 68.05l22.63-22.63" stroke-linecap="round"/>
    </svg>`,

    spinner: `<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16">
        <path d="M128 24a104 104 0 0 1 104 104" stroke-linecap="round">
            <animateTransform attributeName="transform" type="rotate" from="0 128 128" to="360 128 128" dur="1s" repeatCount="indefinite"/>
        </path>
    </svg>`,
};

// ── Icon Helper Function ─────────────────────────────────────────────────────

/**
 * Get an SVG icon by name with optional size
 * @param {string} name - Icon name from ICONS registry
 * @param {number} size - Width and height in pixels (default: 14)
 * @returns {string} SVG markup
 */
export function icon(name, size = 14) {
    const svg = ICONS[name];
    if (!svg) return '';

    // Add width and height attributes
    return svg.replace('<svg', `<svg width="${size}" height="${size}"`);
}

/**
 * Get an icon with custom attributes
 * @param {string} name - Icon name
 * @param {Object} attrs - Additional attributes
 * @returns {string} SVG markup
 */
export function iconWithAttrs(name, attrs = {}) {
    const svg = ICONS[name];
    if (!svg) return '';

    const attrStr = Object.entries(attrs)
        .map(([key, value]) => `${key}="${value}"`)
        .join(' ');

    return svg.replace('<svg', `<svg ${attrStr}`);
}
