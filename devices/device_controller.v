// A minimal "controller" that wants to write/read a byte to/from a peripheral.
// Stands in for e.g. a microcontroller's peripheral-facing bus, per
// devices/CONTRACT.md.
module device_controller (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    input  wire        rw,          // 0 = write, 1 = read
    input  wire [7:0]  target_addr,
    input  wire [7:0]  write_data,
    output reg  [7:0]  read_data,
    output reg         busy,
    output reg         done
);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            busy <= 1'b0;
            done <= 1'b0;
        end else if (start && !busy) begin
            busy <= 1'b1;
            done <= 1'b0;
        end else if (busy) begin
            busy <= 1'b0;
            done <= 1'b1;
        end
    end

endmodule
