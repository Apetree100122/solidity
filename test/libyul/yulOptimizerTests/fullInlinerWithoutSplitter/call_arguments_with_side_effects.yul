{
    function fun_revert() -> ret { revert(0, 0) }
    function fun_return() -> ret { return(0, 0) }
    function empty(a, b) {}

    // Evaluation order in Yul is right to left so fun_revert() should run first.
    empty(fun_return(), fun_revert())
}
// ----
// step: fullInlinerWithoutSplitter
//
// {
//     {
//         let ret_1_3 := 0
//         return(0, 0)
//         let a_1 := ret_1_3
//         let ret_4 := 0
//         revert(0, 0)
//         let b_2 := ret_4
//     }
//     function fun_revert() -> ret
//     { revert(0, 0) }
//     function fun_return() -> ret_1
//     { return(0, 0) }
//     function empty(a, b)
//     { }
// }
