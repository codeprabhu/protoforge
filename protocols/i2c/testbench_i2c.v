`timescale 1ns/1ps
//
// Hand-written I2C testbench -- the trusted oracle for every generated
// top_glue attempt. This file is NEVER generated or edited by the LLM.
//
// It instantiates whatever `top_glue` was compiled alongside it, drives
// tb_start, and acts as a bus-functional-model (BFM) I2C slave that ACKs
// address 0x50 (write) and any data byte, so a standalone master can be
// exercised without needing a full slave device model.
//
// It detects START/STOP/ACK events by watching the shared SDA/SCL wires
// directly (the same way a logic analyzer would), and prints machine
// -readable EVENT:/RESULT: lines that tools/simulation_tool.py parses.

module testbench_i2c;

    reg clk = 0;
    reg rst_n = 0;
    reg tb_start = 0;
    wire tb_done;
    wire sda, scl;

    // Bus pull-ups (open-drain bus model)
    pullup(sda);
    pullup(scl);

    top_glue dut (
        .clk(clk),
        .rst_n(rst_n),
        .sda(sda),
        .scl(scl),
        .tb_start(tb_start),
        .tb_done(tb_done)
    );

    // 100MHz-ish test clock
    always #5 clk = ~clk;

    // ---------------------------------------------------------------
    // Event tracking (event level, watches raw bus lines)
    // ---------------------------------------------------------------
    reg sda_d = 1, scl_d = 1;
    integer saw_start = 0;
    integer saw_stop  = 0;
    integer bit_count = 0;      // bits shifted in since last START
    integer byte_count = 0;     // completed bytes since last START
    reg [7:0] shift_reg = 8'h00;
    reg [7:0] captured_addr = 8'h00;
    reg [7:0] captured_data = 8'h00;
    integer saw_ack_addr = 0;
    integer saw_ack_data = 0;
    reg in_transaction = 0;

    // BFM slave ACK driver: pulls SDA low during the ack clock cell when
    // it is this slave's turn to ACK (after byte_count 0 -> addr ack,
    // after byte_count 1 -> data ack). Address must match 0x50<<1|0 = 0xA0
    // for the addr ack to actually be asserted (mirrors real slave behavior).
    reg slave_ack_drive = 0;
    assign sda = slave_ack_drive ? 1'b0 : 1'bz;

    always @(negedge scl) begin
        // Right after a falling edge that ends bit 8 of a byte (bit_count
        // just hit 8), hold SDA low for the 9th (ack) clock cell if
        // appropriate. We arm the driver here and release it on the next
        // negedge (i.e. it spans exactly one SCL high pulse: the ack cell).
        if (slave_ack_drive) begin
            slave_ack_drive <= 0; // release after the ack cell completes
        end else if (bit_count == 8 && byte_count == 0 && shift_reg == 8'hA0) begin
            slave_ack_drive <= 1;
            saw_ack_addr = 1;
            captured_addr = shift_reg;
            $display("EVENT:ADDR:%02h:%0t", shift_reg, $time);
        end else if (bit_count == 8 && byte_count == 1) begin
            slave_ack_drive <= 1;
            saw_ack_data = 1;
            captured_data = shift_reg;
            $display("EVENT:DATA:%02h:%0t", shift_reg, $time);
        end

        if (bit_count == 8) begin
            bit_count = 0;
            byte_count = byte_count + 1;
        end
    end

    // Shift in data bits on SCL rising edge (standard I2C sampling point),
    // but only while not currently in the ack cell driven by the BFM itself.
    always @(posedge scl) begin
        if (in_transaction && !slave_ack_drive && bit_count < 8) begin
            shift_reg = {shift_reg[6:0], sda};
            bit_count = bit_count + 1;
        end
    end

    // START / STOP detection: sampled on the fast testbench clock so we
    // catch SDA transitioning while SCL is high, regardless of edge timing.
    always @(posedge clk) begin
        sda_d <= sda;
        scl_d <= scl;
        if (scl && scl_d && sda_d && !sda) begin
            saw_start = 1;
            in_transaction = 1;
            bit_count = 0;
            byte_count = 0;
            $display("EVENT:START:%0t", $time);
        end
        if (scl && scl_d && !sda_d && sda) begin
            saw_stop = 1;
            in_transaction = 0;
            $display("EVENT:STOP:%0t", $time);
        end
    end

    // ---------------------------------------------------------------
    // Stimulus
    // ---------------------------------------------------------------
    initial begin
        $dumpfile("generated/testbench_i2c.vcd");
        $dumpvars(0, testbench_i2c);

        rst_n = 0;
        tb_start = 0;
        #20 rst_n = 1;
        #20 tb_start = 1;
        #10 tb_start = 0;

        wait (tb_done || $time > 5000);

        if ($time > 5000) $display("EVENT:TIMEOUT");

        #50; // let any trailing STOP condition settle before we sample

        $display("RESULT:saw_start=%0d saw_stop=%0d saw_ack_addr=%0d saw_ack_data=%0d addr=%02h data=%02h",
                  saw_start, saw_stop, saw_ack_addr, saw_ack_data, captured_addr, captured_data);
        $finish;
    end

endmodule
