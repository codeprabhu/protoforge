// Reference (hand-written, known-correct) I2C master implementing the
// top_glue contract from protocols/i2c/spec.md. Used ONLY to validate
// that testbench_i2c.v actually detects a correct transaction.
//
// Implements the v1 required transaction: START, write address 0x50
// (write bit = 0 -> byte 0xA0), wait for ACK, write one data byte
// (0x3C, arbitrary), wait for ACK, STOP.

module top_glue (
    input  wire clk,
    input  wire rst_n,
    inout  wire sda,
    inout  wire scl,
    input  wire tb_start,
    output reg  tb_done
);

    localparam ADDR_BYTE = 8'hA0; // 0x50 << 1 | write(0)
    localparam DATA_BYTE = 8'h3C; // arbitrary payload

    localparam HALF = 4; // clk cycles per SCL half-period

    // States
    localparam IDLE       = 0,
               START      = 1,
               START_HOLD = 10,
               BIT_LOW    = 2,
               BIT_HIGH   = 3,
               ACK_LOW    = 4,
               ACK_HIGH   = 5,
               NEXT_BYTE  = 6,
               STOP_LOW   = 7,
               STOP_HIGH  = 8,
               DONE       = 9;

    reg [3:0] state = IDLE;
    reg [2:0] bit_idx = 0;
    reg [1:0] byte_idx = 0; // 0 = address, 1 = data
    reg [7:0] cur_byte = 8'h00;
    reg [7:0] half_cnt = 0;

    reg sda_oe = 0;   // 1 = drive low, 0 = release (bus pulls high)
    reg scl_oe = 0;   // 1 = drive low, 0 = release (bus pulls high)

    assign sda = sda_oe ? 1'b0 : 1'bz;
    assign scl = scl_oe ? 1'b0 : 1'bz;

    task automatic advance_half;
        begin
            if (half_cnt == HALF - 1) half_cnt <= 0;
            else half_cnt <= half_cnt + 1;
        end
    endtask

    wire half_done = (half_cnt == HALF - 1);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state    <= IDLE;
            sda_oe   <= 0;
            scl_oe   <= 0;
            tb_done  <= 0;
            half_cnt <= 0;
            bit_idx  <= 0;
            byte_idx <= 0;
        end else begin
            case (state)

                IDLE: begin
                    sda_oe  <= 0;
                    scl_oe  <= 0;
                    tb_done <= 0;
                    if (tb_start) begin
                        cur_byte <= ADDR_BYTE;
                        byte_idx <= 0;
                        state    <= START;
                        half_cnt <= 0;
                    end
                end

                // SCL held high (released) while SDA falls -> START condition
                START: begin
                    scl_oe <= 0; // SCL released/high
                    if (half_done) begin
                        sda_oe   <= 1; // drive SDA low: START condition
                        state    <= START_HOLD;
                        half_cnt <= 0;
                        // NOTE: scl_oe intentionally NOT changed this cycle --
                        // SCL must stay high for a full period after SDA
                        // falls, or the fall looks simultaneous with SCL
                        // dropping and no valid START window ever exists.
                    end else begin
                        advance_half;
                    end
                end

                // Hold SDA low with SCL still high for one more full period
                // so the START condition has an observable high-SCL window,
                // then begin clocking the first bit.
                START_HOLD: begin
                    scl_oe <= 0; // SCL still released/high
                    if (half_done) begin
                        half_cnt <= 0;
                        scl_oe   <= 1; // now pull SCL low to begin bit clocking
                        bit_idx  <= 3'd7;
                        state    <= BIT_LOW;
                    end else begin
                        advance_half;
                    end
                end

                // SCL low: set up the current bit's SDA value (safe to change)
                BIT_LOW: begin
                    scl_oe <= 1; // SCL low
                    sda_oe <= cur_byte[bit_idx] ? 1'b0 : 1'b1; // 1=release(high),0=drive low -> invert
                    if (half_done) begin
                        half_cnt <= 0;
                        scl_oe   <= 0; // release SCL -> rising edge, bit visible
                        state    <= BIT_HIGH;
                    end else begin
                        advance_half;
                    end
                end

                // SCL high: hold bit value, then move to next bit or ack
                BIT_HIGH: begin
                    scl_oe <= 0;
                    if (half_done) begin
                        half_cnt <= 0;
                        scl_oe   <= 1; // pull SCL low again (falling edge)
                        if (bit_idx == 0) begin
                            sda_oe <= 0; // release SDA for slave to drive ACK
                            state  <= ACK_LOW;
                        end else begin
                            bit_idx <= bit_idx - 1;
                            state   <= BIT_LOW;
                        end
                    end else begin
                        advance_half;
                    end
                end

                // SCL low during ack setup: keep SDA released
                ACK_LOW: begin
                    scl_oe <= 1;
                    sda_oe <= 0; // released, let slave drive ack
                    if (half_done) begin
                        half_cnt <= 0;
                        scl_oe   <= 0; // release SCL -> rising edge, sample ack
                        state    <= ACK_HIGH;
                    end else begin
                        advance_half;
                    end
                end

                ACK_HIGH: begin
                    scl_oe <= 0;
                    // (a stricter master would sample sda here and branch on NACK;
                    //  v1 assumes ACK is always given, per spec.md scope)
                    if (half_done) begin
                        half_cnt <= 0;
                        scl_oe   <= 1; // pull SCL low again
                        state    <= NEXT_BYTE;
                    end else begin
                        advance_half;
                    end
                end

                NEXT_BYTE: begin
                    if (byte_idx == 0) begin
                        cur_byte <= DATA_BYTE;
                        byte_idx <= 1;
                        bit_idx  <= 3'd7;
                        state    <= BIT_LOW;
                    end else begin
                        state <= STOP_LOW;
                    end
                end

                // Ensure SDA is low while SCL is low, then release SCL,
                // then release SDA while SCL is high -> STOP condition.
                STOP_LOW: begin
                    scl_oe <= 1;
                    sda_oe <= 1; // drive SDA low
                    if (half_done) begin
                        half_cnt <= 0;
                        scl_oe   <= 0; // release SCL -> goes high
                        state    <= STOP_HIGH;
                    end else begin
                        advance_half;
                    end
                end

                STOP_HIGH: begin
                    scl_oe <= 0;
                    if (half_done) begin
                        sda_oe <= 0; // release SDA while SCL high -> STOP condition
                        state  <= DONE;
                    end else begin
                        advance_half;
                    end
                end

                DONE: begin
                    tb_done <= 1;
                end

                default: state <= IDLE;
            endcase
        end
    end

endmodule
