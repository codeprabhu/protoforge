// A minimal "timer" peripheral exposing a simple start/period/tick interface.
// Stands in for a real device datasheet, per devices/CONTRACT.md.
module device_timer (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    input  wire [7:0]  period,
    output reg         tick
);

    reg [7:0] count;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            count <= 8'h00;
            tick  <= 1'b0;
        end else if (start) begin
            if (count >= period) begin
                count <= 8'h00;
                tick  <= 1'b1;
            end else begin
                count <= count + 1'b1;
                tick  <= 1'b0;
            end
        end else begin
            tick <= 1'b0;
        end
    end

endmodule
