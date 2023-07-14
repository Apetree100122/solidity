library L {
    function f() internal pure { }
}

contract C {
    function g() internal pure { }
    function test(bool b) public returns(bytes4) {
        (b ? L.f : C.g).selector;
    }
}
// ----
// TypeError 9582: (157-181): Member "selector" not found or not visible after argument-dependent lookup in function () pure.
