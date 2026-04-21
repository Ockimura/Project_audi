# Mass Site Audit Script

## Description

This script is designed for automated large-scale auditing of web pages based on a predefined list of URLs.

The main purpose of the script is to identify technical and runtime issues that may affect the accessibility, usability, and overall stability of web interfaces.

The script performs automated browser-based analysis using Playwright and collects information about different types of errors occurring during page loading and execution.

---

## What is analyzed

For each URL, the script performs the following checks:

### 1. Page loading
- Successful page opening
- Navigation errors (timeouts, unreachable pages, etc.)
- Final resolved URL

### 2. JavaScript execution
- Ability to execute JavaScript (`page.evaluate`)
- Basic DOM access validation

### 3. Runtime errors
- Unhandled JavaScript errors (`pageerror`)
- Console errors (`console.error`)
- Console warnings (`console.warn`)

### 4. Network errors
- Failed resource requests (images, scripts, styles, etc.)
- Failed API calls (XHR / fetch requests)

### 5. CDP (Chrome DevTools Protocol)
- Ability to capture page snapshot via `Page.captureSnapshot`
- Detection of CDP-related failures

### 6. DOM state
- DOM content availability
- HTML size (as a proxy for page completeness)
- Page title extraction

### 7. Visual diagnostics (optional)
- Screenshot capture for pages with detected errors

---

## Output

The script generates the following files:

### 1. CSV summary
`mass_audit_results/mass_audit_summary.csv`

Contains aggregated data for each page:
- URL and final URL
- Status of checks (open, JS, CDP, HTML)
- Number of errors (console, runtime, network, API)
- Examples of detected errors
- Screenshot path (if available)

### 2. JSONL detailed log
`mass_audit_results/mass_audit_details.jsonl`

Contains full structured data for each page, including all collected error details.

### 3. Screenshots (optional)
Saved for pages with errors: mass_audit_results/*.png


---

## How to use

### Step 1. Prepare URL list

Create a file named: urls.txt

Add one URL per line:

https://example.com

https://sledcom.ru

https://gov-site.ru

---

### Step 2. Run the script

Make sure your virtual environment is activated, then run:

```
bash
.venv\Scripts\python.exe mass_site_audit.py
```

or press "run python file" in CSV

---

### Step 3. Wait for completion

The script will:

- open each URL in a browser
- collect diagnostics
- save results

Progress will be shown in the terminal.

## Configuration

You can adjust the following parameters in the script:

```HEADLESS``` — run browser in headless mode
```NAV_TIMEOUT_MS``` — page load timeout
```POST_LOAD_WAIT_MS``` — delay after load (for JS execution)
```MAX_CONCURRENT``` — number of parallel pages
```SAVE_SCREENSHOT_ON_ERROR``` — enable screenshots

## Use in research

This script is intended as a preprocessing step for accessibility and interaction analysis.

It allows:

- filtering out broken or unstable pages
- identifying technical issues affecting user experience
- collecting baseline error metrics across multiple sites

The results can be further used for:

- component-level accessibility analysis
- correlation between technical errors and UI issues
- clustering of websites based on stability and error density

## Error Classification

To support further analysis, all detected issues are grouped into several categories.  
This classification allows interpreting technical problems in terms of their impact on accessibility and user interaction.

### 1. Navigation Errors
Errors that prevent the page from loading correctly.

Includes:
- Timeout errors
- DNS resolution failures
- Connection errors
- Invalid or unreachable URLs

Impact:
- Page is not accessible to users
- No further analysis is possible

---

### 2. JavaScript Execution Errors
Errors related to the inability to execute JavaScript in the page context.

Includes:
- `page.evaluate` failures
- Missing DOM elements during script execution
- Broken JS initialization

Impact:
- Interactive components may not function
- Dynamic content may not be rendered

---

### 3. Runtime Errors (Client-side)
Errors occurring during page execution in the browser.

Includes:
- Unhandled JavaScript exceptions (`pageerror`)
- `console.error` messages

Examples:
- ReferenceError
- TypeError
- Undefined variables

Impact:
- Broken UI behavior
- Non-functional buttons, forms, or navigation
- Degraded user experience

---

### 4. Console Warnings
Non-critical issues reported in the browser console.

Includes:
- `console.warn` messages
- Deprecated API usage
- Minor configuration issues

Impact:
- Usually does not break functionality
- May indicate potential future problems

---

### 5. Network Errors
Failures in loading page resources.

Includes:
- Failed requests for:
  - images
  - stylesheets
  - scripts
- Blocked resources (e.g., CSP restrictions)

Impact:
- Missing visual elements
- Broken layouts
- Partial functionality

---

### 6. API Errors
Failures in asynchronous data requests.

Includes:
- Failed XHR requests
- Failed `fetch` requests
- Backend/API unavailability

Impact:
- Missing dynamic content
- Forms not working
- Data not loading

---

### 7. CDP Errors (Browser-Level)
Errors related to Chrome DevTools Protocol operations.

Includes:
- Failure of `Page.captureSnapshot`
- Issues with browser debugging interface

Impact:
- Indicates instability in page structure or browser interaction
- May affect advanced analysis tools

---

### 8. DOM Integrity Issues
Indicators of incomplete or unstable page structure.

Includes:
- Empty or very small DOM
- Missing `<body>` content
- Abnormally low number of elements

Impact:
- Page content not fully rendered
- Incorrect analysis results
- Potential accessibility issues

---

## Interpretation

The classification enables grouping pages into categories such as:

- **Stable pages** — no critical errors detected
- **Partially broken pages** — minor or moderate issues
- **Critical pages** — severe errors affecting usability

This classification can be used for:
- filtering datasets
- prioritizing manual analysis
- correlating technical issues with accessibility violations