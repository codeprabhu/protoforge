"""
Reproduces the exact bug reported live: a generated top_glue using
`#10 { a <= 0, b <= 1 }` inside a clocked always block. Confirms the
structural gate catches it on attempt 0 (before ever calling iverilog)
and that repair recovers.
"""
import json
import os

import agent.ollama_client as ollama_client

# The exact code the user's real Ollama run produced (trimmed of surrounding
# prose) -- embedded directly so this test is self-contained and doesn't
# depend on any external fixture file.
USER_REPORTED_BUG = """
module top_glue (
    input  wire clk,
    input  wire rst_n,
    inout  wire sda,
    inout  wire scl,
    input  wire tb_start,
    output reg  tb_done
);

reg  a_reg_write;
reg  [7:0] a_reg_addr;
reg  [7:0] a_reg_wdata;
wire [7:0] a_reg_rdata;
wire a_data_ready;

device_sensor u_device_a (
        .clk(clk), .rst_n(rst_n), .reg_write(a_reg_write), .reg_addr(a_reg_addr),
        .reg_wdata(a_reg_wdata), .reg_rdata(a_reg_rdata), .data_ready(a_data_ready)
    );

reg  b_start, b_rw;
reg  [7:0] b_target_addr, b_write_data;
wire [7:0] b_read_data;
wire b_busy, b_done;

device_controller u_device_b (
        .clk(clk), .rst_n(rst_n), .start(b_start), .rw(b_rw),
        .target_addr(b_target_addr), .write_data(b_write_data),
        .read_data(b_read_data), .busy(b_busy), .done(b_done)
    );

reg [2:0] state;
reg sda_reg;
reg scl_reg;

parameter IDLE = 3'b000, START = 3'b001, ADDRESS = 3'b010,
          ACK_ADDRESS = 3'b011, DATA_WRITE = 3'b100, ACK_DATA = 3'b101, STOP = 3'b110;

always @(posedge clk or negedge rst_n) begin
if (!rst_n) begin
state <= IDLE;
tb_done <= 0;
end else begin
case (state)
IDLE:
if (tb_start)
state <= START;
START:
b_start <= 1;
sda_reg <= 1;
scl_reg <= 1;
#10 {
sda_reg <= 0,
scl_reg <= 0
                    }
state <= ADDRESS;
ADDRESS:
b_target_addr <= 8'b10100000;
sda_reg <= b_target_addr[7];
#10 {
scl_reg <= 1
                    }
state <= ACK_ADDRESS;
ACK_ADDRESS:
if (!sda) begin
state <= DATA_WRITE;
end else begin
tb_done <= 1;
end
DATA_WRITE:
b_write_data <= 8'h00;
sda_reg <= b_write_data[7];
#10 {
scl_reg <= 1
                    }
state <= ACK_DATA;
ACK_DATA:
if (!sda) begin
state <= STOP;
end else begin
tb_done <= 1;
end
STOP:
b_start <= 0;
sda_reg <= 1;
scl_reg <= 1;
#10 {
sda_reg <= 0,
scl_reg <= 0
                    }
state <= IDLE;
tb_done <= 1;
endcase
end
end

assign sda = sda_reg ? 1'bz : 1'b0;
assign scl = scl_reg;

endmodule
"""

GOOD = open("protocols/i2c/reference/good_master.v").read()

call_count = {"n": 0}


def fake_generate_verilog(prompt, model=None, temperature=None):
    call_count["n"] += 1
    if call_count["n"] == 1:
        print("[stub] first call -> returning the exact user-reported buggy generation")
        return f"```verilog\n{USER_REPORTED_BUG}\n```"
    else:
        print(f"[stub] call #{call_count['n']} (repair) -> returning GOOD master")
        return f"```verilog\n{GOOD}\n```"


ollama_client.generate_verilog = fake_generate_verilog

from agent_core import agent  # noqa: E402

if os.path.exists(agent.RESULTS_LOG):
    os.remove(agent.RESULTS_LOG)

result = agent.run_once(model="stub-model")
print()
print(json.dumps(result, indent=2))

assert result["attempts"][0]["stage"] == "structural"
assert "no_delay_statements" in result["attempts"][0]["failed_checks"]
assert "no_brace_statement_blocks" in result["attempts"][0]["failed_checks"]
assert result["final"] == "pass"

print()
print("ALL ASSERTIONS PASSED - the exact reported bug is now caught pre-compile, and repair recovers.")
