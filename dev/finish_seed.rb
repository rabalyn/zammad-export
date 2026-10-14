# Runs inside the Zammad rails container after dev/seed_zammad.py:
# spreads the dummy tickets over the last ~14 months and creates the restricted agent token
# that zammad-archive uses (ticket.agent only, like a real personal token).
UserInfo.current_user_id = 1
rng = Random.new(42)
tickets = Ticket.joins(:group).where(groups: { name: ["1st Level", "2nd Level", "Netzwerk"] }).order(:id)
tickets.each_with_index do |t, i|
  start = Time.zone.now - (420 - i * 20 + rng.rand(10)).days
  t.articles.order(:id).each_with_index do |a, j|
    a.update_columns(created_at: start + (j * 5).hours, updated_at: start + (j * 5).hours)
  end
  last = start + (t.articles.count * 5).hours
  t.update_columns(created_at: start, updated_at: last, close_at: (t.state.name == "closed" ? last : nil))
  t.reload.search_index_update_backend
end

julia = User.find_by!(email: "j.agent@example.com")
token = Token.create!(action: "api", name: "zammad-archive", user_id: julia.id, persistent: true,
                      preferences: { permission: %w[ticket.agent] })
puts "AGENT_TOKEN=#{token.token}"
