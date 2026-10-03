(function ($) {
    var composing = false;
    var compositionWatched = false;

    function toKatakana(text) {
        return text.replace(/[\u3041-\u3096]/g, function (ch) {
            return String.fromCharCode(ch.charCodeAt(0) + 0x60);
        });
    }

    function normalizeSearch(text) {
        return toKatakana(String(text || "").normalize("NFKC")).toLowerCase();
    }

    function optionMatches(term, data) {
        var reading = "";
        if (data.element) {
            reading = data.element.getAttribute("data-reading") || "";
        }
        var haystack = normalizeSearch((data.text || "") + " " + reading);
        return haystack.indexOf(term) !== -1;
    }

    function matcher(params, data) {
        if (composing || $.trim(params.term) === "") {
            return data;
        }
        if (data.children) {
            var match = $.extend(true, {}, data);
            match.children = [];
            for (var i = 0; i < data.children.length; i++) {
                var child = matcher(params, data.children[i]);
                if (child) {
                    match.children.push(child);
                }
            }
            return match.children.length ? match : null;
        }
        if (typeof data.text === "undefined") {
            return null;
        }
        return optionMatches(normalizeSearch(params.term), data) ? data : null;
    }

    function watchComposition() {
        if (compositionWatched) {
            return;
        }
        compositionWatched = true;
        $(document).on("compositionstart", ".select2-search__field", function () {
            composing = true;
        });
        $(document).on("compositionend", ".select2-search__field", function () {
            composing = false;
            $(this).trigger("input");
        });
    }

    function options(extra) {
        watchComposition();
        return $.extend(
            {
                allowClear: true,
                language: "ja",
                placeholder: "検索して選ぶ",
                matcher: matcher,
            },
            extra || {}
        );
    }

    window.WorkappSelectSearch = {
        matcher: matcher,
        options: options,
    };
})(jQuery);
