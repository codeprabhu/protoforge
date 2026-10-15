from agent.rtl_cleaner import clean_rtl


def test_cleaner_removes_unterminated_verilog_fence():
    source = "```verilog\nmodule top_glue; endmodule\n"
    assert clean_rtl(source) == "module top_glue; endmodule"
