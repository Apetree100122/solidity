contract C {
    function f() public pure { }
    function g() public pure { }
    function test(bool b) public returns(bytes4) {
        (b ? C.f : C.g).selector;
    }
}
// ----
// TypeError 9582: (138-162): Member "selector" not found or not visible after argument-dependent lookup in function () pure.
