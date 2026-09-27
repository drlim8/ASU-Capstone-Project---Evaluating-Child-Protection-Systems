let fosterCareData = [];
let trendChart;

// Load the foster care CSV
Papa.parse("data/Children in foster care.csv", {
    download: true,
    header: true,
    dynamicTyping: true,

    complete: function(results) {

        // Only keep state records
        fosterCareData = results.data.filter(row =>
            row.LocationType === "State" &&
            row.Location &&
            row.TimeFrame &&
            row.Data !== null
        );

        setupDashboard();
    },

    error: function(error) {
        console.error("Could not load CSV:", error);
    }
});


function setupDashboard() {

    loadStates();
    loadYears();

    // Start the dashboard with Minnesota
    let stateSelect = document.getElementById("stateSelect");

    if ([...stateSelect.options].some(option => option.value === "Minnesota")) {
        stateSelect.value = "Minnesota";
    }

    // Start with the newest year in the dataset
    document.getElementById("yearSelect").selectedIndex = 0;

    updateDashboard();
}


function loadStates() {

    const stateSelect = document.getElementById("stateSelect");

    const states = [...new Set(
        fosterCareData.map(row => row.Location)
    )].sort();

    states.forEach(state => {

        const option = document.createElement("option");

        option.value = state;
        option.textContent = state;

        stateSelect.appendChild(option);
    });

    stateSelect.addEventListener("change", updateDashboard);
}


function loadYears() {

    const yearSelect = document.getElementById("yearSelect");

    const years = [...new Set(
        fosterCareData.map(row => row.TimeFrame)
    )].sort((a, b) => b - a);

    years.forEach(year => {

        const option = document.createElement("option");

        option.value = year;
        option.textContent = year;

        yearSelect.appendChild(option);
    });

    yearSelect.addEventListener("change", updateDashboard);
}


function updateDashboard() {

    const state = document.getElementById("stateSelect").value;
    const year = Number(document.getElementById("yearSelect").value);

    updateMainMetric(state, year);
    updateTrendChart(state);
    updateComparisonTable(year);
}


function updateMainMetric(state, year) {

    const record = fosterCareData.find(row =>
        row.Location === state &&
        Number(row.TimeFrame) === year
    );

    const numberElement = document.getElementById("fosterCareNumber");
    const infoElement = document.getElementById("metricInfo");

    if (record) {

        numberElement.textContent =
            Number(record.Data).toLocaleString();

        infoElement.textContent =
            state + " - " + year;

    } else {

        numberElement.textContent = "No Data";
        infoElement.textContent =
            "No foster care value is available for this selection.";
    }
}


function updateTrendChart(state) {

    let stateData = fosterCareData
        .filter(row => row.Location === state)
        .sort((a, b) => a.TimeFrame - b.TimeFrame);

    const years = stateData.map(row => row.TimeFrame);
    const values = stateData.map(row => row.Data);

    if (trendChart) {
        trendChart.destroy();
    }

    const ctx = document
        .getElementById("trendChart")
        .getContext("2d");

    trendChart = new Chart(ctx, {

        type: "line",

        data: {
            labels: years,

            datasets: [{
                label: "Children in Foster Care",
                data: values,
                borderWidth: 2,
                tension: 0.2
            }]
        },

        options: {
            responsive: true,

            scales: {
                y: {
                    beginAtZero: false
                }
            }
        }
    });
}


function updateComparisonTable(year) {

    const table = document.getElementById("comparisonTable");

    table.innerHTML = "";

    const yearData = fosterCareData
        .filter(row => Number(row.TimeFrame) === year)
        .sort((a, b) => b.Data - a.Data);

    yearData.forEach(record => {

        const row = document.createElement("tr");

        const stateCell = document.createElement("td");
        const yearCell = document.createElement("td");
        const dataCell = document.createElement("td");

        stateCell.textContent = record.Location;
        yearCell.textContent = record.TimeFrame;
        dataCell.textContent =
            Number(record.Data).toLocaleString();

        row.appendChild(stateCell);
        row.appendChild(yearCell);
        row.appendChild(dataCell);

        table.appendChild(row);
    });
}