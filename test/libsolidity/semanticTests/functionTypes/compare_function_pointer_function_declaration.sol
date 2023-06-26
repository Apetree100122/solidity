contract C {
    function f() internal {}
    function g() internal {}

    function testDifferent() public pure returns (bool) {
        function () internal ptr = C.f;
        return ptr == C.g;
    }

    function testEqual() public pure returns (bool) {
        function () internal ptr = C.f;
        return ptr == C.f;
    }
}
// ----
// testDifferent() -> false
// testEqual() -> true
