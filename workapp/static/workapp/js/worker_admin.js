(function() {
    function toggleOwnRates() {
        var checkbox = document.getElementById("id_use_common_rate");
        var inline = document.getElementById("workerrate_set-group");
        if (!checkbox || !inline) {
            return;
        }
        inline.style.display = checkbox.checked ? "none" : "";
    }

    function bind() {
        var checkbox = document.getElementById("id_use_common_rate");
        if (!checkbox) {
            return;
        }
        checkbox.addEventListener("change", toggleOwnRates);
        toggleOwnRates();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bind);
    } else {
        bind();
    }
})();
