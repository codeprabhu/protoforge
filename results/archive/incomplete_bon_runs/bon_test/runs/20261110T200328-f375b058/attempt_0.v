module top_glue (
    input  wire clk,
    input  wire rst_n,
    inout  wire sda,
    inout  wire scl,
    input  wire tb_start,
    output reg  tb_done
);

    // Generated deterministically from parsed device interfaces.
    reg  a_reg_write;
    reg  [7:0] a_reg_addr;
    reg  [7:0] a_reg_wdata;
    wire [7:0] a_reg_rdata;
    wire a_data_ready;

    device_sensor u_device_a (
        .clk(clk),
        .rst_n(rst_n),
        .reg_write(a_reg_write),
        .reg_addr(a_reg_addr),
        .reg_wdata(a_reg_wdata),
        .reg_rdata(a_reg_rdata),
        .data_ready(a_data_ready)
    );

    reg  b_start;
    reg  b_rw;
    reg  [7:0] b_target_addr;
    reg  [7:0] b_write_data;
    wire [7:0] b_read_data;
    wire b_busy;
    wire b_done;

    device_controller u_device_b (
        .clk(clk),
        .rst_n(rst_n),
        .start(b_start),
        .rw(b_rw),
        .target_addr(b_target_addr),
        .write_data(b_write_data),
        .read_data(b_read_data),
        .busy(b_busy),
        .done(b_done)
    );

    // Open-drain bus drivers are fixed by the harness.
    reg sda_drive_low;
    reg scl_drive_low;
    assign sda = sda_drive_low ? 1'b0 : 1'bz;
    assign scl = scl_drive_low ? 1'b0 : 1'bz;

    // Model-generated FSM fragment begins here.
    localparam ADDR_BYTE = 8'hA0;
    localparam DATA_BYTE_0 = 8'h3C;
    localparam HALF = 4;

    localparam IDLE = 0, START = 1, START_HOLD = 2, BIT_LOW = 3,
               BIT_HIGH = 4, ACK_LOW = 5, ACK_HIGH = 6, NEXT_BYTE = 7,
               STOP_LOW = 8, STOP_HIGH = 9, DONE = 10;

    reg [3:0]  state;
    reg [2:0]  bit_idx;
    reg [7:0]  byte_idx;
    reg [7:0]  cur_byte;
    reg [7:0]  half_cnt;

    task automatic advance_half;
        begin
            if (half_cnt == HALF - 1) half_cnt <= 0;
            else half_cnt <= half_cnt + 1;
        end
    endtask

    wire half_done = (half_cnt == HALF - 1);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state         <= IDLE;
            sda_drive_low <= 0;
            scl_drive_low <= 0;
            tb_done       <= 0;
            half_cnt      <= 0;
            bit_idx       <= 0;
            byte_idx      <= 0;
            cur_byte      <= 8'h00;
            // Device inputs are held at zero; the oracle observes the I2C bus.
            a_reg_write <= 0;
            a_reg_addr <= 0;
            a_reg_wdata <= 0;
            b_start <= 0;
            b_rw <= 0;
            b_target_addr <= 0;
            b_write_data <= 0;
        end else begin
            case (state)

                IDLE: begin
                    sda_drive_low <= 0;
                    scl_drive_low <= 0;
                    tb_done <= 0;
                    if (tb_start) begin
                        cur_byte <= ADDR_BYTE;
                        byte_idx <= 0;
                        state    <= START;
                        half_cnt <= 0;
                    end
                end

                // SCL high while SDA falls -> START condition
                START: begin
                    scl_drive_low <= 0;
                    if (half_done) begin
                        sda_drive_low <= 1;
                        state    <= START_HOLD;
                        half_cnt <= 0;
                    end else begin
                        advance_half;
                    end
                end

                // Hold the START window for a full period.
                START_HOLD: begin
                    scl_drive_low <= 0;
                    if (half_done) begin
                        half_cnt <= 0;
                        scl_drive_low <= 1;
                        bit_idx  <= 3'd7;
                        state    <= BIT_LOW;
                    end else begin
                        advance_half;
                    end
                end

                // SCL low: change SDA safely.
                BIT_LOW: begin
                    scl_drive_low <= 1;
                    sda_drive_low <= cur_byte[bit_idx] ? 1'b0 : 1'b1;
                    if (half_done) begin
                        half_cnt <= 0;
                        scl_drive_low <= 0;
                        state    <= BIT_HIGH;
                    end else begin
                        advance_half;
                    end
                end

                // SCL high: hold SDA, then advance or start the ACK cell.
                BIT_HIGH: begin
                    scl_drive_low <= 0;
                    if (half_done) begin
                        half_cnt <= 0;
                        scl_drive_low <= 1;
                        if (bit_idx == 0) begin
                            sda_drive_low <= 0;
                            state <= ACK_LOW;
                        end else begin
                            bit_idx <= bit_idx - 1;
                            state   <= BIT_LOW;
                        end
                    end else begin
                        advance_half;
                    end
                end

                ACK_LOW: begin
                    scl_drive_low <= 1;
                    sda_drive_low <= 0;
                    if (half_done) begin
                        half_cnt <= 0;
                        scl_drive_low <= 0;
                        state    <= ACK_HIGH;
                    end else begin
                        advance_half;
                    end
                end

                ACK_HIGH: begin
                    scl_drive_low <= 0;
                    if (half_done) begin
                        half_cnt <= 0;
                        scl_drive_low <= 1;
                        state    <= NEXT_BYTE;
                    end else begin
                        advance_half;
                    end
                end

                NEXT_BYTE: begin
                    if (byte_idx == 0) begin
                        cur_byte <= DATA_BYTE_0;
                        byte_idx <= 1;
                        bit_idx  <= 3'd7;
                        state    <= BIT_LOW;
                    end
                    else begin
                        state <= STOP_LOW;
                    end
                end

                STOP_LOW: begin
                    scl_drive_low <= 1;
                    sda_drive_low <= 1;
                    if (half_done) begin
                        half_cnt <= 0;
                        scl_drive_low <= 0;
                        state    <= STOP_HIGH;
                    end else begin
                        advance_half;
                    end
                end

                STOP_HIGH: begin
                    scl_drive_low <= 0;
                    if (half_done) begin
                        sda_drive_low <= 0;
                        state <= DONE;
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
    // Model-generated FSM fragment ends here.
endmodule
