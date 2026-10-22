import { Container, Text, Title } from '@mantine/core';
import { strings } from './strings';

export function App() {
  return (
    <Container size="md" py="xl">
      <Title order={1}>{strings.appName}</Title>
      <Text c="dimmed">{strings.tagline}</Text>
    </Container>
  );
}
