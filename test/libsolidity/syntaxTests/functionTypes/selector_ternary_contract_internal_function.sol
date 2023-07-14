contract C {
    function f() internal pure { }
    function g() internal pure { }
    function test(bool b) public returns(bytes4) {
        (b ? C.f : C.g).selector;
    }
}
// ----
// TypeError 9582: (142-166): Member "selector" not found or not visible after argument-dependent lookup in function () pure.
