module top_glue (
    input wire clk, input wire rst_n, inout wire sda, inout wire scl,
    input wire tb_start, output reg tb_done
);
    always @(posedge clk) begin
        sda <= 1'b0;
        #10 tb_done <= 1'b1;
    end
endmodule
