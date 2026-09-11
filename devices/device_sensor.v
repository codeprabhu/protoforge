// A minimal "sensor" peripheral exposing a register-file interface.
// Stands in for a real device datasheet, per devices/CONTRACT.md.
module device_sensor (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        reg_write,
    input  wire [7:0]  reg_addr,
    input  wire [7:0]  reg_wdata,
    output reg  [7:0]  reg_rdata,
    output reg         data_ready
);

    reg [7:0] regfile [0:7];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            reg_rdata  <= 8'h00;
            data_ready <= 1'b0;
        end else if (reg_write) begin
            regfile[reg_addr[2:0]] <= reg_wdata;
            data_ready <= 1'b1;
        end else begin
            reg_rdata <= regfile[reg_addr[2:0]];
        end
    end

endmodule
