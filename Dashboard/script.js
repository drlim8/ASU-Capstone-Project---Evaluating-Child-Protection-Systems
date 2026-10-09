let data = [];
let trendChart;

const scopeSelect = document.getElementById("scopeSelect");
const metricSelect = document.getElementById("metricSelect");
const yearSelect = document.getElementById("yearSelect");

Papa.parse("data/kara_clean_data_v2.csv", {
    download: true,
    header: true,
    skipEmptyLines: true,

    complete: function(results) {
        data = results.data;

        populateScopes();
        populateMetrics();
        populateYears();

        scopeSelect.value = "Minnesota";

        const fosterCareMetric = data.find(
            row => row.Field_ID === "BF-001"
        );

        if (fosterCareMetric) {
            metricSelect.value = fosterCareMetric.Field_Name;
        }

        yearSelect.value = getLatestYear();

        updateDashboard();
    },

    error: function(error) {
        console.error("Error loading CSV:", error);
    }
});

scopeSelect.addEventListener("change", function() {
    populateMetrics();
    populateYears();
    updateDashboard();
});

metricSelect.addEventListener("change", function() {
    populateYears();
    updateDashboard();
});

yearSelect.addEventListener("change", updateDashboard);

function populateScopes() {
    const scopes = [...new Set(data.map(row => row.Scope))]
        .filter(Boolean)
        .sort();

    scopeSelect.innerHTML = "";

    scopes.forEach(scope => {
        const option = document.createElement("option");
        option.value = scope;
        option.textContent = scope;
        scopeSelect.appendChild(option);
    });
}

function populateMetrics() {
    const selectedScope = scopeSelect.value;

    let filteredData = data;

    if (selectedScope) {
        filteredData = data.filter(row => row.Scope === selectedScope);
    }

    const metrics = [...new Set(
        filteredData.map(row => row.Field_Name)
    )]
        .filter(Boolean)
        .sort();

    const previousMetric = metricSelect.value;

    metricSelect.innerHTML = "";

    metrics.forEach(metric => {
        const option = document.createElement("option");
        option.value = metric;
        option.textContent = metric;
        metricSelect.appendChild(option);
    });

    if (metrics.includes(previousMetric)) {
        metricSelect.value = previousMetric;
    }
}

function populateYears() {
    const scope = scopeSelect.value;
    const metric = metricSelect.value;

    const years = [...new Set(
        data
            .filter(row =>
                row.Scope === scope &&
                row.Field_Name === metric
            )
            .map(row => row.Year)
    )]
        .filter(Boolean)
        .sort((a, b) => Number(b) - Number(a));

    const previousYear = yearSelect.value;

    yearSelect.innerHTML = "";

    years.forEach(year => {
        const option = document.createElement("option");
        option.value = year;
        option.textContent = year;
        yearSelect.appendChild(option);
    });

    if (years.includes(previousYear)) {
        yearSelect.value = previousYear;
    }
}

function getLatestYear() {
    const years = data
        .map(row => Number(row.Year))
        .filter(year => !isNaN(year));

    return Math.max(...years).toString();
}

function updateDashboard() {
    const scope = scopeSelect.value;
    const metric = metricSelect.value;
    const year = yearSelect.value;

    const selectedRow = data.find(row =>
        row.Scope === scope &&
        row.Field_Name === metric &&
        row.Year === year
    );

    if (!selectedRow) {
        return;
    }

    updateCards(selectedRow);
    updateDetails(selectedRow);
    updateTrendChart(scope, metric);
    updateComparison(metric, year);
}

function updateCards(row) {
    const currentValue = document.getElementById("currentValue");
    const confidence = document.getElementById("confidence");
    const comparability = document.getElementById("comparability");
    const availability = document.getElementById("availability");

    if (hasNumericValue(row.Numeric_Value)) {
        currentValue.textContent = formatValue(row.Numeric_Value);
        availability.textContent = "Available";
    } else {
        currentValue.textContent = row.Missing_Label || "Not Available";
        availability.textContent = row.Missing_Label || "Not Available";
    }

    confidence.textContent = row.Data_Confidence || "Unknown";
    comparability.textContent =
        row.Comparability_Status || "Not Available";
}

function updateDetails(row) {
    document.getElementById("fieldID").textContent =
        row.Field_ID || "--";

    document.getElementById("fieldName").textContent =
        row.Field_Name || "--";

    document.getElementById("detailScope").textContent =
        row.Scope || "--";

    document.getElementById("detailYear").textContent =
        row.Year || "--";

    document.getElementById("originalValue").textContent =
        row.Original_Value || "Not Available";

    document.getElementById("missingLabel").textContent =
        row.Missing_Label || "None";

    document.getElementById("notes").textContent =
        row.Notes || "No notes available.";

    const sourceLink = document.getElementById("sourceLink");

    if (row.Source_URL && row.Source_URL.trim() !== "") {
        sourceLink.href = row.Source_URL;
        sourceLink.style.display = "inline-block";
    } else {
        sourceLink.removeAttribute("href");
        sourceLink.style.display = "none";
    }
}

function updateTrendChart(scope, metric) {
    const rows = data
        .filter(row =>
            row.Scope === scope &&
            row.Field_Name === metric
        )
        .sort((a, b) => Number(a.Year) - Number(b.Year));

    const labels = rows.map(row => row.Year);

    const values = rows.map(row => {
        if (hasNumericValue(row.Numeric_Value)) {
            return Number(row.Numeric_Value);
        }

        return null;
    });

    document.getElementById("trendTitle").textContent =
        `${metric} Over Time - ${scope}`;

    const ctx = document.getElementById("trendChart");

    if (trendChart) {
        trendChart.destroy();
    }

    trendChart = new Chart(ctx, {
        type: "line",

        data: {
            labels: labels,

            datasets: [{
                label: metric,
                data: values,
                borderWidth: 2,
                tension: 0.2,
                spanGaps: false
            }]
        },

        options: {
            responsive: true,
            maintainAspectRatio: false,

            plugins: {
                legend: {
                    display: true
                },

                tooltip: {
                    callbacks: {
                        label: function(context) {
                            if (context.raw === null) {
                                return "Not Available";
                            }

                            return formatValue(context.raw);
                        }
                    }
                }
            },

            scales: {
                y: {
                    beginAtZero: false
                }
            }
        }
    });
}

function updateComparison(metric, year) {
    const comparisonRows = data.filter(row =>
        row.Field_Name === metric &&
        row.Year === year
    );

    const tableBody =
        document.getElementById("comparisonTable");

    tableBody.innerHTML = "";

    document.getElementById("comparisonTitle").textContent =
        `Minnesota vs. National - ${metric} (${year})`;

    comparisonRows.forEach(row => {
        const tr = document.createElement("tr");

        const value = hasNumericValue(row.Numeric_Value)
            ? formatValue(row.Numeric_Value)
            : row.Missing_Label || "Not Available";

        tr.innerHTML = `
            <td>${safeText(row.Scope)}</td>
            <td>${safeText(row.Year)}</td>
            <td>${safeText(value)}</td>
            <td>${safeText(row.Data_Confidence || "Unknown")}</td>
            <td>${safeText(row.Comparability_Status || "Not Available")}</td>
        `;

        tableBody.appendChild(tr);
    });

    if (comparisonRows.length === 0) {
        const tr = document.createElement("tr");

        tr.innerHTML = `
            <td colspan="5">No comparison data available.</td>
        `;

        tableBody.appendChild(tr);
    }
}

function hasNumericValue(value) {
    return value !== null &&
        value !== undefined &&
        value !== "" &&
        !isNaN(Number(value));
}

function formatValue(value) {
    const number = Number(value);

    if (isNaN(number)) {
        return value;
    }

    return number.toLocaleString();
}

function safeText(value) {
    if (value === null || value === undefined) {
        return "";
    }

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}