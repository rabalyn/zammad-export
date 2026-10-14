# Runs inside the Zammad rails container (dev/zammad-dev.sh). Prepares a fresh instance:
# finishes the setup wizard, adds an admin + API token and a dummy outgoing mail address.
UserInfo.current_user_id = 1
Setting.set("system_init_done", true)
Setting.set("fqdn", ENV.fetch("ZAMMAD_FQDN", "localhost:8095"))
Setting.set("http_type", "http")

admin = User.find_by(email: "admin@example.com") || User.create!(
  login: "admin@example.com", email: "admin@example.com", firstname: "Archive", lastname: "Admin",
  active: true, roles: Role.where(name: %w[Admin Agent]), password: SecureRandom.hex(16)
)
admin.group_names_access_map = Group.all.to_h { |g| [g.name, ["full"]] }
admin.save!

# Email articles need a sender address on the group, and delivery must succeed: a failed
# delivery makes Zammad reopen the ticket. Mails go to the Mailpit sink (dev/podmanize_compose.py).
outbound = { adapter: "smtp", options: { host: "mailpit", port: 1025, user: "", password: "",
                                          ssl: false, enable_starttls_auto: false } }
channel = Channel.where(area: "Email::Account").first || Channel.create!(
  area: "Email::Account", active: true, group_id: Group.first.id,
  options: { inbound: { adapter: "null", options: {} }, outbound: outbound }
)
channel.update!(options: channel.options.merge("outbound" => outbound.deep_stringify_keys))
address = EmailAddress.find_by(email: "support@example.com") ||
          EmailAddress.create!(name: "Support", email: "support@example.com", channel_id: channel.id, active: true)
Group.find_each { |g| g.update!(email_address_id: address.id) }

token = Token.create!(action: "api", name: "seed", user_id: admin.id, persistent: true,
                      preferences: { permission: %w[admin ticket.agent] })
puts "ADMIN_TOKEN=#{token.token}"
